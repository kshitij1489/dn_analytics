import uuid
import threading
from typing import Dict, Any

class JobManager:
    _jobs: Dict[str, Dict[str, Any]] = {}

    @classmethod
    def start_job(cls, func, *args, **kwargs) -> str:
        job_id = str(uuid.uuid4())
        cls._jobs[job_id] = {
            "status": "running",
            "message": "Starting...",
            "progress": 0.0,
            "stats": {}
        }
        
        def run():
            try:
                result = func(*args, **kwargs)
                
                # Handle Generator (Streaming progress)
                if hasattr(result, '__iter__') and not isinstance(result, (str, bytes, list, dict)):
                    for status in result:
                         # status is expected to be an object with .type, .message, .progress, .stats
                        cls._jobs[job_id].update({
                            "status": "running" if status.type != 'done' else "completed",
                            "message": status.message,
                            "progress": status.progress,
                            "stats": status.stats or cls._jobs[job_id]["stats"]
                        })
                        if status.type == 'error':
                            cls._jobs[job_id]["status"] = "failed"
                
                # Handle Standard Return (Sync function)
                else:
                    cls._jobs[job_id].update({
                        "status": "completed",
                        "message": "Task completed successfully",
                        "progress": 1.0,
                        "stats": result if isinstance(result, dict) else {}
                    })

            except Exception as e:
                cls._jobs[job_id].update({
                    "status": "failed", 
                    "message": str(e)
                })

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        return job_id

    @classmethod
    def get_job(cls, job_id: str):
        return cls._jobs.get(job_id)

    @classmethod
    def list_jobs(cls):
        return cls._jobs
