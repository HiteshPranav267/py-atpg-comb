from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request
from typing import List, Dict
from pyatpg.api.schemas import (
    BenchmarkStartRequest,
    BenchmarkStatusResponse,
    BenchmarkResultResponse,
    EvaluationResultSchema
)
from pyatpg.api.services.benchmark_service import BenchmarkService
from pyatpg.api.services.ranking_service import RankingService
from pyatpg.core import run_db
from pyatpg.core.analytics_store import get_analytics_store

router = APIRouter(prefix="/api/benchmark", tags=["benchmark"])

@router.get("/rankers", response_model=List[str])
def get_rankers():
    return RankingService.get_available_rankers()


@router.post("/start", response_model=Dict[str, str])
async def start_benchmark(req: BenchmarkStartRequest, request: Request):
    received = datetime.now(timezone.utc)
    try:
        job_id = BenchmarkService.start_benchmark(req.dataset_dir, req.ranker_name, req.k)
        run_db.record_simple(
            run_type="benchmark", endpoint=request.url.path, received_at=received, request=request,
            label=f"Benchmark {req.ranker_name}: {req.dataset_dir}", meta={"job_id": job_id},
            details={"benchmark": {"dataset_dir": req.dataset_dir, "ranker_name": req.ranker_name,
                                   "k": req.k, "status": "started"}},
        )
        get_analytics_store().record(
            "benchmark_run", dataset_dir=req.dataset_dir, ranker_name=req.ranker_name, k=req.k
        )
        return {"job_id": job_id}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/status/{job_id}", response_model=BenchmarkStatusResponse)
def get_status(job_id: str):
    job = BenchmarkService.get_status(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
        
    return BenchmarkStatusResponse(
        job_id=job.job_id,
        status=job.status,
        progress=job.progress,
        message=job.message
    )


@router.get("/result/{job_id}", response_model=BenchmarkResultResponse)
def get_result(job_id: str):
    job = BenchmarkService.get_status(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
        
    results = None
    if job.results:
        results = [EvaluationResultSchema(**r.to_dict()) for r in job.results]
        
    return BenchmarkResultResponse(
        job_id=job.job_id,
        status=job.status,
        results=results,
        error=job.error
    )
