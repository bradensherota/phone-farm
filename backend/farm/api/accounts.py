from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..db import get_session
from ..models import Account, AccountStatus, Device, Platform
from ..schemas import AccountIn, AccountOut, AccountPatch

router = APIRouter(prefix="/accounts", tags=["accounts"])


@router.post("", response_model=AccountOut)
def create_account(body: AccountIn, s: Session = Depends(get_session)):
    if s.get(Device, body.device_id) is None:
        raise HTTPException(404, "device not found")
    try:
        platform = Platform(body.platform)
        status = AccountStatus(body.status)
    except ValueError as e:
        raise HTTPException(400, str(e))
    n = s.query(func.count(Account.id)).filter(Account.device_id == body.device_id).scalar() or 0
    link = (body.story_link or "").strip()
    if not link and platform in (Platform.instagram, Platform.tiktok):
        from .. import settings as settings_mod
        link = settings_mod.get(f"default_link_{platform.value}", "").strip()
    a = Account(
        device_id=body.device_id, platform=platform, handle=body.handle.lstrip("@"),
        story_link=link, notes=body.notes, status=status, sort_order=n,
    )
    s.add(a)
    s.commit()
    s.refresh(a)
    return a


@router.patch("/{account_id}", response_model=AccountOut)
def patch_account(account_id: int, body: AccountPatch, s: Session = Depends(get_session)):
    a = s.get(Account, account_id)
    if a is None:
        raise HTTPException(404, "account not found")
    data = body.model_dump(exclude_unset=True)
    if "status" in data:
        data["status"] = AccountStatus(data["status"])
    if "handle" in data:
        data["handle"] = data["handle"].lstrip("@")
    if "device_id" in data and s.get(Device, data["device_id"]) is None:
        raise HTTPException(404, "device not found")
    for k, v in data.items():
        setattr(a, k, v)
    s.commit()
    s.refresh(a)
    return a


@router.delete("/{account_id}")
def delete_account(account_id: int, s: Session = Depends(get_session)):
    a = s.get(Account, account_id)
    if a is None:
        raise HTTPException(404, "account not found")
    s.delete(a)
    s.commit()
    return {"ok": True}
