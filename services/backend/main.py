from fastapi import FastAPI
from mangum import Mangum

from services.backend.api.routes.agent import router as agent_router

app = FastAPI()
app.include_router(agent_router)


@app.get("/health")
def health():
    return {"status": "healthy"}


handler = Mangum(app)
