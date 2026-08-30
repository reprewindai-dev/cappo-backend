from fastapi import FastAPI, Request, Response
import httpx
import uvicorn

app = FastAPI()

@app.get('/healthz')
def health():
    return {'status': 'ok'}

@app.post('/webhook/governed-webhook')
async def webhook(request: Request):
    data = await request.json()
    authority = data.get('veklom_authority')
    content = data.get('data', {})
    
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.post(
                'http://127.0.0.1:8099/governed-action',
                json=content,
                headers={'Authorization': f'Bearer {authority}'} if authority else {}
            )
            return Response(content=resp.content, status_code=resp.status_code, headers=dict(resp.headers))
        except Exception as e:
            return Response(content=str(e), status_code=500)

if __name__ == '__main__':
    uvicorn.run(app, host='127.0.0.1', port=5678)
