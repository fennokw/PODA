"""Import normalized LLM archives into PODA's visible memory graph."""
from __future__ import annotations

import json
import math
import re
import uuid
from collections import Counter
from datetime import datetime, timezone
from typing import Any

import numpy as np

from ..runtime import config, ollama
from ..runtime.db import connect, now_iso, one, rows, set_setting
from ..memory import store, surface, layout
from ..memory.embedding_jobs import enqueue as enqueue_embeddings
from .archive import ImportConversation, ImportMessage
from .profile import derive as derive_profile

TOPIC_STOP = {"the","and","for","that","this","with","you","your","from","have","what","when","how","can","will","just","about","into","like","want","need","more","some","all","also","was","are","but","not","get","got","use","using","please","thanks","would","could","should"}


def _conversation_text(conv: ImportConversation) -> str:
    user = "\n".join(m.text for m in conv.messages if m.role == "user")
    return f"{conv.title}\n{user}"[:12000]


def _terms(text: str, limit: int = 10) -> list[str]:
    words = re.findall(r"[A-Za-z][A-Za-z0-9_+.-]{2,}", (text or "").lower())
    return [w for w, _ in Counter(w for w in words if w not in TOPIC_STOP).most_common(limit)]


def _cluster(conversations: list[ImportConversation]) -> list[list[int]]:
    if len(conversations) <= 1:
        return [[0]] if conversations else []
    docs = [_conversation_text(c) for c in conversations]
    try:
        vecs = []
        for offset in range(0, len(docs), 32):
            vecs.extend(store.normalize(v) for v in ollama.embed(docs[offset:offset + 32], timeout=600))
    except Exception:
        vecs = []
    if vecs and len(vecs) == len(docs):
        target = max(4, min(18, round(math.sqrt(len(docs) / 2)) + 2))
        threshold = 0.58 if len(docs) < 100 else 0.62
        clusters: list[dict[str, Any]] = []
        for i, vec in enumerate(vecs):
            v = np.asarray(vec, dtype=float)
            best = None; best_cos = -2.0
            for c in clusters:
                centroid = c["sum"] / (np.linalg.norm(c["sum"]) or 1.0)
                score = float(centroid @ v)
                if score > best_cos:
                    best, best_cos = c, score
            if best is not None and (best_cos >= threshold or len(clusters) >= target):
                best["ids"].append(i); best["sum"] += v
            else:
                clusters.append({"ids":[i], "sum":v.copy()})
        return [c["ids"] for c in clusters]
    # Offline deterministic fallback: group by top title/user terms.
    buckets: dict[str, list[int]] = {}
    for i, conv in enumerate(conversations):
        terms = _terms(_conversation_text(conv), 4)
        key = terms[0] if terms else "general"
        buckets.setdefault(key, []).append(i)
    # Avoid dozens of singleton clouds offline: fold small buckets into General.
    general: list[int] = []
    out: list[list[int]] = []
    for ids in buckets.values():
        if len(ids) < 2 and len(conversations) > 12:
            general.extend(ids)
        else:
            out.append(ids)
    if general:
        out.append(general)
    return out


def _label_cluster(conversations: list[ImportConversation], ids: list[int], number: int) -> tuple[str, str, list[str]]:
    titles = [conversations[i].title for i in ids[:30]]
    text = "\n".join(_conversation_text(conversations[i])[:1600] for i in ids[:12])
    terms = _terms(" ".join(titles) + " " + text, 12)
    prompt = """Name a high-level personal knowledge cloud from these conversation titles/excerpts. Return ONLY JSON: {\"title\": \"2-5 words\", \"summary\": \"one sentence\", \"tags\": [\"...\"]}. Keep it broad enough to contain related future conversations; do not invent facts or include private specifics in the title.\n\n""" + "\n".join(f"- {t}" for t in titles[:25]) + "\n\nEXCERPTS:\n" + text[:12000]
    try:
        installed = ollama.installed_models(timeout=2.0)
        model = next((m for m in installed if "qwen" in m.lower()), None) or (installed[0] if installed else None)
        if model:
            resp = ollama.chat([{"role":"user","content":prompt}], model, options={"temperature":0.05,"num_ctx":16384,"num_predict":240}, think=False if model.startswith("qwen") else None, timeout=180)
            raw = (resp.get("message",{}).get("content") or "").strip()
            match = re.search(r"\{.*\}", raw, re.S)
            data = json.loads(match.group(0) if match else raw)
            title = str(data.get("title") or "").strip()[:80]
            summary = str(data.get("summary") or "").strip()[:420]
            tags = [str(x).strip()[:40] for x in (data.get("tags") or []) if str(x).strip()][:12]
            if title:
                return title, summary or f"Imported conversations related to {title}.", tags or terms
    except Exception:
        pass
    label = " ".join(t.replace("-", " ").title() for t in terms[:3]) or f"Imported Topic {number}"
    return label[:80], f"Imported conversations grouped around {', '.join(terms[:6]) or 'general context'}.", terms


def _create_domain(conn, import_id: str, title: str, summary: str, tags: list[str], idx: int) -> str:
    nid = str(uuid.uuid4())
    angle = idx * 2.399963229728653
    radius = 180 + 18 * math.sqrt(idx + 1)
    x, y, z = radius * math.cos(angle), radius * math.sin(angle), ((idx % 5) - 2) * 55
    now = now_iso()
    all_tags = ["imported", "topic-cloud", *tags]
    conn.execute("""INSERT INTO memory_nodes(id,parent_id,title,node_type,summary,content,reference_triggers,tags_json,x,y,z,semantic_x,semantic_y,semantic_z,position_source,pinned,importance,memory_kind,speaker,confidence,source_ref,created_at,updated_at)
                  VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                 (nid, store.ROOT_ID, title, "domain", summary, summary, ", ".join(tags[:12]), json.dumps(all_tags[:20]), x, y, z, x, y, z,
                  "semantic", 1, 0.72, "domain", "system", 0.8, f"llm-import:{import_id}", now, now))
    conn.execute("INSERT OR IGNORE INTO memory_links(id,source_id,target_id,relation,strength,created_at,updated_at) VALUES(?,?,?,'contains',0.84,?,?)",
                 (str(uuid.uuid4()), store.ROOT_ID, nid, now, now))
    return nid


def _iso(value: str | None) -> str:
    if not value:
        return now_iso()
    try:
        # unix-like ChatGPT timestamps are sometimes encoded as strings.
        f = float(value)
        return datetime.fromtimestamp(f, tz=timezone.utc).isoformat()
    except Exception:
        pass
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc).isoformat()
    except Exception:
        return now_iso()


def _pairs(messages: list[ImportMessage]) -> list[tuple[ImportMessage, ImportMessage | None]]:
    out: list[tuple[ImportMessage, ImportMessage | None]] = []
    pending: ImportMessage | None = None
    for m in messages:
        if m.role == "user":
            if pending:
                out.append((pending, None))
            pending = m
        elif m.role == "assistant" and pending:
            out.append((pending, m)); pending = None
    if pending:
        out.append((pending, None))
    return out


def import_conversations(provider: str, conversations: list[ImportConversation], source_filename: str, archive_fingerprint: str,
                         apply_profile: bool = True, parser_meta: dict[str, Any] | None = None) -> dict[str, Any]:
    conn = connect()
    import_id = str(uuid.uuid4())
    created_ids: list[str] = []
    domains: list[dict[str, Any]] = []
    try:
        existing = one(conn, "SELECT * FROM llm_imports WHERE fingerprint=?", (archive_fingerprint,))
        if existing:
            return {"status":"already_imported", "import_id":existing["id"], "provider":existing["provider"], "conversations":existing["conversation_count"], "memories":existing["memory_count"]}
        clusters = _cluster(conversations)
        conv_domain: dict[int, str] = {}
        for ci, ids in enumerate(clusters):
            title, summary, tags = _label_cluster(conversations, ids, ci + 1)
            did = _create_domain(conn, import_id, title, summary, tags, ci)
            domains.append({"id":did,"title":title,"summary":summary,"conversations":len(ids)})
            for idx in ids:
                conv_domain[idx] = did
        message_count = 0; memory_count = 0
        now = now_iso()
        for ci, conv in enumerate(conversations):
            parent = conv_domain.get(ci, store.CONVERSATION_CLOUD)
            session = f"import-{import_id}-{conv.source_id}"[:220]
            message_ids: dict[int, str] = {}
            for mi, m in enumerate(conv.messages):
                mid = str(uuid.uuid4()); message_ids[mi] = mid
                conn.execute("INSERT INTO messages(id,role,content,model,created_at,session_id,provenance,metadata_json) VALUES(?,?,?,?,?,?,?,?)",
                             (mid,m.role,m.text,None,_iso(m.created_at),session,f"import:{provider}",json.dumps({"import_id":import_id,"conversation_id":conv.source_id,"conversation_title":conv.title,"source_message_id":m.source_id})))
                message_count += 1
            # Pair from normalized messages while mapping IDs by object identity.
            id_by_obj = {id(m): message_ids[i] for i,m in enumerate(conv.messages)}
            for user, assistant in _pairs(conv.messages):
                nid = str(uuid.uuid4())
                title = store.memory_title(user.text)
                full = f"You: {user.text}" + (f"\n\nAssistant: {assistant.text}" if assistant else "")
                summary = (" ".join(user.text.split())[:300] + ((" — Assistant: " + " ".join(assistant.text.split())[:110]) if assistant else ""))[:420]
                tags = store.extract_terms(conv.title + " " + user.text, limit=12)
                trigger = ", ".join(tags[:10])
                ts = _iso(user.created_at)
                conn.execute("""INSERT INTO memory_nodes(id,parent_id,title,node_type,summary,content,reference_triggers,tags_json,x,y,z,pinned,importance,memory_kind,source_message_id,response_message_id,user_text,assistant_text,speaker,confidence,source_ref,created_at,updated_at)
                              VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                             (nid, parent, title[:180], "memory", summary, full[:24000], trigger, json.dumps(["imported",provider,*tags][:20]),
                              None, None, None, 0, 0.48, "exchange", id_by_obj[id(user)], id_by_obj.get(id(assistant)) if assistant else None,
                              user.text[:12000], assistant.text[:12000] if assistant else None, "exchange", 0.65, f"llm-import:{import_id}:{conv.source_id}", ts, ts))
                conn.execute("INSERT OR IGNORE INTO memory_links(id,source_id,target_id,relation,strength,created_at,updated_at) VALUES(?,?,?,'contains',0.72,?,?)",
                             (str(uuid.uuid4()),parent,nid,ts,ts))
                created_ids.append(nid); memory_count += 1
        profile = derive_profile(conversations) if apply_profile else {}
        if apply_profile:
            profile_json = json.dumps(profile, ensure_ascii=False)
            set_setting("imported_user_profile", profile_json, conn=conn)
            set_setting("imported_user_profile_updated_at", now, conn=conn)
            # Make the profile itself visible in the second brain.
            pid = str(uuid.uuid4())
            profile_cloud = str(uuid.uuid4())
            conn.execute("""INSERT INTO memory_nodes(id,parent_id,title,node_type,summary,content,reference_triggers,tags_json,x,y,z,semantic_x,semantic_y,semantic_z,position_source,pinned,importance,memory_kind,speaker,confidence,source_ref,created_at,updated_at)
                          VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                         (profile_cloud, store.ROOT_ID, "Imported Personalization", "domain", "Patterns derived from user-authored imported conversations.",
                          "Patterns derived from user-authored imported conversations.", "preferences, workflow, response style, recurring topics",
                          json.dumps(["imported","profile","preferences"]), 220, -160, 90, 220, -160, 90, "semantic", 1, 0.88, "domain", "system", 0.85,
                          f"llm-import:{import_id}", now, now))
            conn.execute("INSERT OR IGNORE INTO memory_links(id,source_id,target_id,relation,strength,created_at,updated_at) VALUES(?,?,?,'contains',0.86,?,?)",
                         (str(uuid.uuid4()),store.ROOT_ID,profile_cloud,now,now))
            summary = str(profile.get("summary") or "Imported personalization profile")
            conn.execute("""INSERT INTO memory_nodes(id,parent_id,title,node_type,summary,content,reference_triggers,tags_json,x,y,z,pinned,importance,memory_kind,speaker,confidence,source_ref,created_at,updated_at)
                          VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                         (pid, profile_cloud, "Imported workflow & response profile", "memory", summary, json.dumps(profile,ensure_ascii=False)[:24000],
                          "communication style, workflow preferences, how should you answer, personalization", json.dumps(["imported","profile","preferences","workflow"]),
                          None, None, None, 1, 0.9, "durable", "user", 0.75, f"llm-import:{import_id}:profile", now, now))
            created_ids.append(pid); memory_count += 1
        conn.execute("""INSERT INTO llm_imports(id,provider,source_filename,fingerprint,conversation_count,message_count,memory_count,cloud_count,profile_json,parser_meta_json,created_at)
                      VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                     (import_id,provider,source_filename[:260],archive_fingerprint,len(conversations),message_count,memory_count,len(domains),json.dumps(profile,ensure_ascii=False) if profile else None,json.dumps(parser_meta or {}),now))
        enqueue_embeddings(conn, created_ids)
        conn.commit()
    except Exception:
        conn.rollback(); raise
    finally:
        conn.close()
    # Best-effort immediate vector refresh + surface organization. Failure does not undo import.
    embedded = 0; surface_result = None
    try:
        from ..memory.embedding_jobs import refresh_pending
        while True:
            r = refresh_pending(64); embedded += int(r.get("refreshed") or 0)
            if not r.get("pending") or r.get("error") or embedded >= 512:
                break
        conn = connect()
        try:
            surface_result = surface.rebuild(conn, "incremental", distill_limit=30)
            layout.semantic_layout(conn, clear_manual_positions=False)
            conn.commit()
        finally:
            conn.close()
    except Exception:
        surface_result = None
    return {"status":"imported","import_id":import_id,"provider":provider,"conversations":len(conversations),"messages":message_count,"memories":memory_count,
            "clouds":domains,"profile":profile,"embeddings_refreshed":embedded,"surface_rebuild":surface_result}


def history() -> list[dict[str, Any]]:
    conn = connect()
    try:
        return rows(conn, "SELECT id,provider,source_filename,conversation_count,message_count,memory_count,cloud_count,created_at FROM llm_imports ORDER BY created_at DESC")
    finally:
        conn.close()
