from pydantic import BaseModel
from typing import Optional, List

class ProcessStatus(BaseModel):
    pid: int
    script: str
    config_path: str
    start_time: str
    status: str

class StartProcessRequest(BaseModel):
    script: str
    config_path: str
