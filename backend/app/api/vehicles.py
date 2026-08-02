"""Fahrzeug-Verwaltung (mehrere Fahrzeuge, SoC-Quelle, Ziel-SoC)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..models import User, Vehicle
from ..schemas import VehicleCreate, VehicleOut
from ..security import get_current_user, require_role

router = APIRouter(prefix="/api/vehicles", tags=["vehicles"])


@router.get("", response_model=list[VehicleOut])
async def list_vehicles(db: AsyncSession = Depends(get_db), _: User = Depends(get_current_user)):
    return (await db.scalars(select(Vehicle).order_by(Vehicle.id))).all()


@router.post("", response_model=VehicleOut)
async def create_vehicle(body: VehicleCreate, db: AsyncSession = Depends(get_db), _: User = Depends(require_role("admin"))):
    vehicle = Vehicle(**body.model_dump())
    db.add(vehicle)
    await db.commit()
    await db.refresh(vehicle)
    return vehicle


@router.patch("/{vehicle_id}", response_model=VehicleOut)
async def update_vehicle(vehicle_id: int, body: VehicleCreate, db: AsyncSession = Depends(get_db), _: User = Depends(require_role("admin"))):
    vehicle = await db.get(Vehicle, vehicle_id)
    if vehicle is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Fahrzeug nicht gefunden")
    for key, value in body.model_dump().items():
        setattr(vehicle, key, value)
    await db.commit()
    await db.refresh(vehicle)
    return vehicle


@router.delete("/{vehicle_id}", status_code=204)
async def delete_vehicle(vehicle_id: int, db: AsyncSession = Depends(get_db), _: User = Depends(require_role("admin"))):
    vehicle = await db.get(Vehicle, vehicle_id)
    if vehicle:
        await db.delete(vehicle)
        await db.commit()
