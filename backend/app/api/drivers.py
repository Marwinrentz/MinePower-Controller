"""Treiber-Katalog für den Setup-Assistenten: Metadaten + Konfig-Felder."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from ..drivers import registry
from ..drivers.base import DriverMeta
from ..models import User
from ..security import get_current_user

router = APIRouter(prefix="/api/drivers", tags=["drivers"])


@router.get("", response_model=list[DriverMeta])
async def list_drivers(category: str | None = None, _: User = Depends(get_current_user)):
    if category:
        return registry.metas_for(category)
    return registry.all_metas()
