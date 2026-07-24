import asyncio
import uuid
from typing import Dict, Any, Optional
from pyatpg.evaluation.benchmark import BenchmarkRunner
from pyatpg.api.services.ranking_service import RankingService


class BenchmarkJob:
    def __init__(self, job_id: str):
        self.job_id = job_id
        self.status = "RUNNING" # RUNNING, COMPLETED, FAILED
        self.progress = 0.0
        self.message = "Initializing..."
        self.results = None
        self.error = None


# Global in-memory job store (acceptable for single-tenant desktop API)
_JOBS: Dict[str, BenchmarkJob] = {}

class BenchmarkService:
    @staticmethod
    def start_benchmark(dataset_dir: str, ranker_name: str, k: int = 5) -> str:
        job_id = str(uuid.uuid4())
        job = BenchmarkJob(job_id)
        _JOBS[job_id] = job
        
        # We start the blocking benchmark runner in a background thread
        # so we don't block the FastAPI event loop
        asyncio.create_task(BenchmarkService._run_job(job_id, dataset_dir, ranker_name, k))
        
        return job_id
        
    @staticmethod
    async def _run_job(job_id: str, dataset_dir: str, ranker_name: str, k: int):
        job = _JOBS[job_id]
        try:
            ranker = RankingService.get_ranker(ranker_name)
            job.message = f"Starting Benchmark Runner for {ranker_name}"
            # Give UI time to poll initial status
            await asyncio.sleep(0.5) 
            
            runner = BenchmarkRunner(dataset_dir=dataset_dir, ranker=ranker, k=k)
            # This is a blocking call. In a prod server we'd use run_in_executor,
            # but for a local desktop app this is fine inside create_task for now.
            # Real progress reporting would require injecting a callback into BenchmarkRunner.
            # For now, we jump from 0 -> 100 on completion.
            
            job.message = "Running evaluations across dataset..."
            results = runner.run()
            
            job.results = results
            job.progress = 100.0
            job.status = "COMPLETED"
            job.message = "Benchmark finished."
        except Exception as e:
            job.status = "FAILED"
            job.error = str(e)
            job.message = "Benchmark failed."
            
    @staticmethod
    def get_status(job_id: str) -> Optional[BenchmarkJob]:
        return _JOBS.get(job_id)
