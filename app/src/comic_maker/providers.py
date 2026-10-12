"""Bounded provider calls; credentials never enter manifests or error messages."""
from __future__ import annotations
import base64
import io
import json
import time
from urllib.parse import quote
import cv2
import httpx
import numpy as np
from PIL import Image
from .imaging import png_bytes, MAX_PIXELS
from .settings import DEFAULT_PROMPT

class ProviderFailure(Exception):
    pass

def post(client, url, reserve, **kwargs):
    reserve()  # Each HTTP attempt consumes the persisted batch call allowance.
    try:
        response=client.post(url, **kwargs)
    except httpx.TransportError as exc:
        raise ProviderFailure('Provider connection failed or timed out') from exc
    if response.status_code in (401,403):
        raise ProviderFailure('Provider rejected the API key or model access')
    if response.status_code == 429:
        try:
            code=response.json().get('error',{}).get('code','')
        except (ValueError,AttributeError): code=''
        if code in ('insufficient_quota','billing_hard_limit_reached'):
            raise ProviderFailure('Provider account quota is exhausted; check billing before retrying')
        raise ProviderFailure('Provider rate limit reached')
    if response.status_code >= 500:
        raise ProviderFailure('Provider temporarily unavailable')
    if response.status_code >= 400:
        raise ProviderFailure(f'Provider rejected the request (HTTP {response.status_code}); check model and settings')
    return response.json()

def decode(data):
    raw=base64.b64decode(data,validate=True)
    with Image.open(io.BytesIO(raw)) as img:
        if img.width*img.height>MAX_PIXELS:
            raise ProviderFailure('Provider returned an oversized image')
        img.load()
        return img.convert('RGB')

def request_color(image, settings, key, reserve, client=None, correction=False):
    if settings.provider == 'fixture':
        # Exact, deterministic test adapter. Never presented as generated artwork.
        arr=np.asarray(image.convert('RGB')).copy()
        yy,xx=np.indices(arr.shape[:2])
        mask=(arr.min(axis=2)>240)&(((xx//120+yy//120)%3)==0)
        arr[mask]=(238,210,165)
        return Image.fromarray(arr)
    if not key:
        raise ProviderFailure(f'Add a {settings.provider} API key in Settings to enable colorization')
    prompt=settings.prompt.strip() or DEFAULT_PROMPT
    if correction:
        prompt+='\nThe earlier attempt changed the drawing. Prioritize exact lettering, silhouettes and all original line positions. Use lighter fills so the black lines remain distinct.'
    raw=png_bytes(image)
    owns=client is None
    client=client or httpx.Client(timeout=httpx.Timeout(300,connect=30),follow_redirects=False)
    try:
        if settings.provider == 'openai':
            w,h=image.size
            if settings.image_model.startswith('gpt-image-2.5'):
                scale=min(settings.image_edge/max(w,h),(8_000_000/(w*h))**.5)
                scale=max(scale,(700_000/(w*h))**.5)
                width,height=[max(16,int(n*scale)//16*16) for n in (w,h)]
                # Very wide strips are padded by the caller to a supported ratio.
                size=f'{width}x{height}'
                quality=settings.image_quality
            else:
                size='1536x1024' if w/h>1.2 else '1024x1536' if h/w>1.2 else '1024x1024'
                quality='high'
            body=post(client,'https://api.openai.com/v1/images/edits',reserve,
                headers={'Authorization':f'Bearer {key}'},data={'model':settings.image_model,'prompt':prompt,'size':size,'quality':quality,'output_format':'png','n':'1'},
                files={'image':('comic.png',raw,'image/png')})
            return decode(body['data'][0]['b64_json'])
        ratio=image.width/image.height
        ratios={'1:1':1,'3:2':1.5,'2:3':2/3,'4:3':4/3,'3:4':.75,'16:9':16/9,'9:16':9/16,'21:9':21/9}
        aspect=min(ratios,key=lambda k:abs(ratios[k]-ratio))
        body=post(client,f'https://generativelanguage.googleapis.com/v1beta/models/{quote(settings.image_model,safe="")}:generateContent',reserve,
            headers={'x-goog-api-key':key},json={'contents':[{'parts':[{'text':prompt},{'inlineData':{'mimeType':'image/png','data':base64.b64encode(raw).decode()}}]}],
            'generationConfig':{'responseModalities':['IMAGE'],'imageConfig':{'aspectRatio':aspect,'imageSize':{1024:'1K',2048:'2K',3840:'4K'}[settings.image_edge]}}})
        for candidate in body.get('candidates',[]):
            for part in candidate.get('content',{}).get('parts',[]):
                if 'inlineData' in part:
                    return decode(part['inlineData']['data'])
        raise ProviderFailure('Provider returned no image')
    except (KeyError,ValueError,TypeError) as exc:
        raise ProviderFailure('Provider returned an unreadable image response') from exc
    finally:
        if owns:
            client.close()

def vision_boxes(image, settings, key, reserve, client=None):
    if not key:
        raise ProviderFailure('Vision recovery needs an OpenAI API key')
    thumb=image.copy();thumb.thumbnail((1800,1800))
    prompt=('Find comic panels in reading order. Ignore scanner registration marks, notes outside panels, and objects within panels. '
        'Include complete hand-drawn borders and a small white margin. Return JSON only: {"boxes":[{"x":0,"y":0,"w":1000,"h":1000}]}. '
        'Coordinates are normalized integers on a 0 to 1000 scale for the entire image. Do not invent panels.')
    owns=client is None;client=client or httpx.Client(timeout=120)
    try:
        body=post(client,'https://api.openai.com/v1/responses',reserve,headers={'Authorization':f'Bearer {key}'},
            json={'model':settings.vision_model,'input':[{'role':'user','content':[{'type':'input_text','text':prompt},{'type':'input_image','image_url':'data:image/png;base64,'+base64.b64encode(png_bytes(thumb)).decode()}]}],
                  'text':{'format':{'type':'json_object'}}})
        text=''.join(part.get('text','') for item in body.get('output',[]) for part in item.get('content',[]) if part.get('type')=='output_text')
        values=json.loads(text)['boxes']
        return [{k:round(float(b[k])*(image.width if k in ('x','w') else image.height)/1000) for k in ('x','y','w','h')} for b in values]
    except (KeyError,ValueError,TypeError) as exc:
        raise ProviderFailure('Vision recovery returned invalid panel coordinates') from exc
    finally:
        if owns: client.close()

def fidelity(original, result):
    """A structural alarm, not proof of semantic or lettering correctness."""
    src=original.convert('L');src.thumbnail((1400,1400))
    dst=result.resize(src.size,Image.Resampling.BOX)
    source=(np.asarray(src)<110).astype(np.uint8)
    target=(np.asarray(dst.convert('RGB')).max(axis=2)<95).astype(np.uint8)
    near=cv2.dilate(target,np.ones((3,3),np.uint8))
    recall=float((near & source).sum()/max(1,source.sum()))
    ratio=float(target.sum()/max(1,source.sum()))
    return {'line_recall':round(recall,4),'dark_area_ratio':round(ratio,4),'suspect':recall<.80 or ratio>2.5 or ratio<.45,
            'note':'Automatic structural check; exact lettering and faces are not guaranteed.'}

def transient(exc):
    return any(word in str(exc) for word in ('temporarily','connection','rate'))

def backoff(attempt):
    time.sleep(min(8,2**attempt))
