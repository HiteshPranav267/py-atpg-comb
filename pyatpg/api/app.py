from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pyatpg.api.routes import circuit, feature, ranking, benchmark, trojan, atpg, analytics

app = FastAPI(
    title="PyATPG API",
    description="Stateless REST API for Hardware Trojan localization, feature extraction, ranking, and benchmarking.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition", "X-Circuit-Name", "X-Variants-Generated", "X-Strategy"],
)

app.include_router(circuit.router)
app.include_router(feature.router)
app.include_router(ranking.router)
app.include_router(benchmark.router)
app.include_router(trojan.router)
app.include_router(atpg.router)
app.include_router(analytics.router)


@app.get("/health")
def health_check():
    return {"status": "healthy"}


@app.get("/")
def root():
    return {
        "name": "PyATPG API",
        "status": "healthy",
        "docs": "/docs",
        "health": "/health",
    }
