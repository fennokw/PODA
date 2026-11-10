"""Short-lived Outlook Classic COM worker. The parent bounds the runtime."""
from __future__ import annotations

from datetime import timezone
import json
import sys

from .models import MailError, address_hash, mask_address, original_sent_timestamp, split_sender

REMEDY = "Open classic Outlook for Windows and sign in to a mail profile."
MAIL_ITEM = 43
FLAG_WITHOUT_DUE_DATE = 4
DEFAULT_FOLDERS = {"INBOX": 6, "Drafts": 16, "Sent": 5, "Trash": 3}
MAPI = "http://schemas.microsoft.com/mapi/proptag/"
SCAN_LIMIT = 5000


def _items(collection):
    for number in range(1, collection.Count + 1):
        yield collection.Item(number)


def _accounts(namespace):
    stores = set()
    for account in _items(namespace.Accounts):
        store = account.DeliveryStore
        address = str(account.SmtpAddress or "").strip()
        if not store or not address or store.StoreID in stores:
            continue
        stores.add(store.StoreID)
        label = " ".join(mask_address(str(account.DisplayName or address)).split()).replace("\x1f", " ")
        yield account, store, f"{label} · {address_hash(address)[:8]}", address


def _folders(store):
    defaults = {}
    for name, kind in DEFAULT_FOLDERS.items():
        try:
            folder = store.GetDefaultFolder(kind)
            if folder:
                defaults[folder.EntryID] = name
        except Exception:
            continue

    def walk(collection, prefix="", depth=0):
        if depth == 15:
            return
        for folder in _items(collection):
            name = defaults.get(folder.EntryID) or (prefix + folder.Name)
            if folder.DefaultItemType == 0:
                yield name, folder
            yield from walk(folder.Folders, name + "/", depth + 1)

    return dict(walk(store.GetRootFolder().Folders))


def _optional(call, default=""):
    try:
        return call() or default
    except Exception:
        return default


def _smtp(entry):
    if not entry:
        return ""
    if str(entry.Type).upper() == "EX":
        return _optional(lambda: entry.GetExchangeUser().PrimarySmtpAddress) or _optional(
            lambda: entry.PropertyAccessor.GetProperty(MAPI + "0x39FE001F"))
    return str(entry.Address or "")


def _sender(item):
    address = _optional(lambda: _smtp(item.Sender)) or _optional(lambda: item.SenderEmailAddress)
    return f"{item.SenderName or ''} <{address}>" if address else ""


def _row(item, *, include_sent=False):
    date = _optional(lambda: item.ReceivedTime, None)
    sent = _optional(lambda: item.SentOn, None) if include_sent else None
    headers = _optional(lambda: item.PropertyAccessor.GetProperty(MAPI + "0x007D001F"), "") if include_sent else ""
    sent_at, known = original_sent_timestamp(headers, sent.astimezone(timezone.utc).isoformat() if sent else None)
    return {
        "native_id": item.EntryID, "subject": str(item.Subject or ""), "sender": _sender(item),
        "date": date.astimezone(timezone.utc).isoformat() if date else None,
        "sent_at": sent_at, "sender_timezone_known": known,
        "read": not item.UnRead, "flagged": item.FlagStatus == 2,
        "message_id": _optional(lambda: item.PropertyAccessor.GetProperty(MAPI + "0x1035001F"), None),
    }


def _flags(item):
    return {"read": not item.UnRead, "flagged": item.FlagStatus == 2}


def _find(namespace, store, folder, entry_id):
    try:
        item = namespace.GetItemFromID(entry_id, store.StoreID)
    except Exception:
        item = None
    if not item or item.Class != MAIL_ITEM or item.Parent.EntryID != folder.EntryID:
        raise MailError("not_found", "The message is no longer in the selected Outlook folder.", status=404)
    return item


def _mailboxes(folders):
    return [{"name": name, "total": folder.Items.Count, "unread": folder.UnReadItemCount,
             "kind": name.lower() if name in DEFAULT_FOLDERS else None} for name, folder in folders.items()]


def _list(folder, args):
    items = folder.Items
    if args.get("unread_only"):
        items = items.Restrict("[UnRead] = True")
    items.Sort("[ReceivedTime]", True)
    limit, offset = int(args.get("limit", 50)), int(args.get("offset", 0))
    search = str(args.get("query") or "").casefold()
    found = []
    scanned = 0
    total = items.Count
    truncated = False
    if not search:
        # Read one extra match to distinguish a full page from the end of the folder.
        indices = range(offset + 1, min(total, offset + limit + 1) + 1)
    else:
        indices = range(1, min(total, SCAN_LIMIT) + 1)
    skipped = 0
    for i in indices:
        scanned += 1
        item = items.Item(i)
        if item.Class != MAIL_ITEM:
            continue
        if search and search not in f"{item.Subject or ''} {_sender(item)}".casefold():
            continue
        if search and skipped < offset:
            skipped += 1
            continue
        if len(found) == limit:
            truncated = bool(search)
            break
        found.append(_row(item))
    if not search and len(found) < min(limit, max(0, total - offset)):
        truncated = True
    if search:
        truncated |= scanned < total
        total = offset + len(found) + int(truncated)
    return {"rows": found, "total": total, "unread": folder.UnReadItemCount, "scanned": scanned,
            "truncated": truncated}


def _draft(namespace, account, store, args):
    folder = store.GetDefaultFolder(DEFAULT_FOLDERS["Drafts"])
    item = folder.Items.Add("IPM.Note")
    item.SendUsingAccount = account
    sender = str(account.SmtpAddress or "").casefold()
    if not item.SendUsingAccount or str(item.SendUsingAccount.SmtpAddress or "").casefold() != sender:
        raise MailError("provider", "Outlook did not confirm the selected sending account.", status=502)
    for kind, addresses in ((1, args["to"]), (2, args.get("cc", []))):
        for value in addresses:
            _, address = split_sender(value)
            if "@" not in address or any(ch in address for ch in "\r\n;,"):
                raise MailError("validation", "Outlook recipients must be exact email addresses.", status=400)
            recipient = item.Recipients.Add(address)
            recipient.Type = kind
            if not recipient.Resolve() or _smtp(recipient.AddressEntry).casefold() != address.casefold():
                raise MailError("validation", "Outlook did not resolve a recipient to the supplied address.", status=400)
    item.Subject = args["subject"]
    item.Body = args["body"]
    if args.get("send"):
        item.Send()
        return {"sent": True}
    item.Save()
    saved = _find(namespace, store, folder, item.EntryID)
    return {"saved": True, "draft_id": saved.EntryID}


def dispatch(namespace, operation, args):
    if operation == "status":
        return {"running": True, "accounts": [{"account_name": name, "addresses": [address],
                 "provider_type": "outlook_classic", "enabled": True} for _, _, name, address in _accounts(namespace)]}
    selected = next((a for a in _accounts(namespace) if a[2] == args.get("account")), None)
    if selected is None:
        raise MailError("not_found", "The selected account is missing from the classic Outlook profile.", REMEDY, status=404)
    account, store, _, _ = selected
    if operation == "draft":
        return _draft(namespace, account, store, args)
    folders = _folders(store)
    if operation == "mailboxes":
        return {"mailboxes": _mailboxes(folders)}
    folder = folders.get(args.get("mailbox"))
    if folder is None:
        raise MailError("not_found", "The selected Outlook folder was not found.", status=404)
    if operation == "list":
        return _list(folder, args)
    if operation == "snippets":
        return {entry: _find(namespace, store, folder, entry).Body[:1500] for entry in args["ids"]}
    item = _find(namespace, store, folder, args["native_id"])
    if operation == "read":
        reply = _optional(lambda: _smtp(item.ReplyRecipients.Item(1).AddressEntry))
        recipients = [(r.Type, _optional(lambda: _smtp(r.AddressEntry))) for r in _items(item.Recipients)]
        attachments = [{"name": item.Attachments.Item(i).FileName, "size": item.Attachments.Item(i).Size}
                       for i in range(1, min(item.Attachments.Count, 30) + 1)]
        return {**_row(item, include_sent=True), "body": str(item.Body or "")[:args["max_chars"]], "reply_to": reply,
                "to": [addr for kind, addr in recipients if kind == 1],
                "cc": [addr for kind, addr in recipients if kind == 2], "attachments": attachments}
    if operation == "set_flags":
        if args.get("read") is not None:
            item.UnRead = not args["read"]
        if args.get("flagged") is not None:
            if args["flagged"]:
                item.MarkAsTask(FLAG_WITHOUT_DUE_DATE)
            else:
                item.ClearTaskFlag()
        item.Save()
        return _flags(_find(namespace, store, folder, item.EntryID))
    if operation in {"move", "trash"}:
        target_name = "Trash" if operation == "trash" else args["target"]
        target = folders.get(target_name)
        if target is None:
            raise MailError("not_found", "The target Outlook folder was not found.", status=404)
        moved = item.Move(target)
        _find(namespace, store, target, moved.EntryID)
        return {"moved": True, "trashed": operation == "trash", "mailbox": target_name}
    raise MailError("validation", "Unknown Outlook operation.", status=400)


def main():
    import pythoncom
    from win32com.client import Dispatch

    pythoncom.CoInitialize()
    try:
        request = json.load(sys.stdin)
        try:
            app = Dispatch("Outlook.Application")
            namespace = app.GetNamespace("MAPI")
        except Exception as exc:
            raise MailError("mail_not_running", "Classic Outlook is unavailable or has no open profile.", REMEDY, status=409) from exc
        result = {"result": dispatch(namespace, request["operation"], request["args"])}
    except MailError as exc:
        result = {"error": exc.message, "kind": exc.kind, "status": exc.status, "remedy": exc.remedy}
    except Exception:
        result = {"error": "Classic Outlook refused the operation. Check its profile or security prompt.",
                  "kind": "provider", "status": 502, "remedy": REMEDY}
    finally:
        pythoncom.CoUninitialize()
    print(json.dumps(result, ensure_ascii=True))


if __name__ == "__main__":
    main()
