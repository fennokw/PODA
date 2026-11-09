"""PODA chat tools for creating recurring Daily Updates, always with approval."""
from __future__ import annotations
from typing import Any
from ..agent.tools import _schema, _str, _bool
from . import service

def list_daily_updates() -> dict[str,Any]:
    items=service.tasks()
    return {"ok":True,"result":{"schedules":[{k:v for k,v in t.items() if k!="times_json"} for t in items],
          "recent_runs":[{k:r.get(k) for k in ("id","prompt_id","title","scheduled_for","status","response")}
                         for r in service.history(12)]},
          "error":None,"verification":f"read {len(items)} local daily schedules"}

def create_daily_update(title:str,prompt:str,times:list[str],timezone:str,enabled:bool=True)->dict[str,Any]:
    try:
        saved=service.save(title,prompt,times,timezone,enabled)
        return {"ok":True,"result":{"id":saved["id"],"title":saved["title"],"times":saved["times"],
                   "timezone":saved["timezone"],"enabled":saved["enabled"]},
                "error":None,"verification":"saved recurring prompt in local encrypted database"}
    except Exception as exc:
        return {"ok":False,"result":None,"error":str(exc),"verification":"nothing scheduled"}

TOOLS={
 "daily_updates_list":{"description":"List the user's scheduled recurring Daily Updates and recent results, read-only.",
    "mutating":False,"requires_confirmation":False,"needs":None,
    "handler":list_daily_updates,"schema":_schema({},[])},
 "daily_updates_create":{"description":"Create a recurring daily PODA prompt, executing at 1-12 times daily with the user's specified local timezone, saving results to Daily Updates. User confirmation is mandatory before scheduling. Only schedules while the PODA app is running.",
    "mutating":True,"requires_confirmation":True,"needs":None,
    "handler":create_daily_update,
    "schema":_schema({"title":_str("Short name"),"prompt":_str("Exact daily question to run"),
     "times":{"type":"array","items":{"type":"string"},"description":"24h times HH:MM"},
     "timezone":_str("IANA timezone such as America/New_York"),
     "enabled":_bool("Should start enabled")},["title","prompt","times","timezone"])}
}
