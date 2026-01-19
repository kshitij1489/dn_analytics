from pydantic import BaseModel
from typing import List, Optional, Any, Dict

class JobResponse(BaseModel):
    job_id: str
    status: str
    message: str
    progress: float
    stats: Optional[Dict[str, Any]] = None

class QueryRequest(BaseModel):
    query: str
