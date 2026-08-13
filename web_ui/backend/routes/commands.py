"""Read API for lifecycle command progress."""

from fastapi import APIRouter, Depends, HTTPException, Query, status

from auth import verify_token
from command_repository import GridCommandRepository
from schemas import GridCommandListResponse, GridCommandResponse

router = APIRouter(prefix="/api/v1/commands", tags=["commands"])


@router.get("", response_model=GridCommandListResponse)
async def list_grid_commands(
    grid_id: str = Query(..., min_length=1),
    limit: int = Query(default=20, ge=1, le=100),
    _token: str = Depends(verify_token),
):
    return {"grid_id": grid_id, "commands": GridCommandRepository.instance().list_for_grid(grid_id, limit)}


@router.get("/{command_id}", response_model=GridCommandResponse)
async def get_command(command_id: str, _token: str = Depends(verify_token)):
    command = GridCommandRepository.instance().get(command_id)
    if command is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="命令不存在")
    return command
