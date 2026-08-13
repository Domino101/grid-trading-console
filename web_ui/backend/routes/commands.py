"""Read API for lifecycle command progress."""

from fastapi import APIRouter, Depends, HTTPException, status

from auth import verify_token
from command_repository import GridCommandRepository
from schemas import GridCommandResponse

router = APIRouter(prefix="/api/v1/commands", tags=["commands"])


@router.get("/{command_id}", response_model=GridCommandResponse)
async def get_command(command_id: str, _token: str = Depends(verify_token)):
    command = GridCommandRepository.instance().get(command_id)
    if command is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="命令不存在")
    return command
