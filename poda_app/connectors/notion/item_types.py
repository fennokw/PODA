"""Type-first semantics for normalized Notion properties; kinds never change Type."""
TASK_TYPES = {"task", "homework", "test", "assignment", "quiz", "exam"}


def text(value):
    if isinstance(value, list):
        value = value[0] if value else None
    return str(value or "").strip()


def is_task(type_value, source=None):
    value = text(type_value).casefold()
    # Import provenance is only a legacy fallback when Type is absent.
    return value in TASK_TYPES if value else text(source).casefold() == "canvas"


def interpret(props, names):
    def get(key, fallback):
        return props.get(names.get(key) or fallback)
    type_value = get("type", "Type")
    task = is_task(type_value, get("source", "Import Source"))
    task_kind, event_kind = get("task_kind", "Task kind"), get("event_kind", "Event kind")
    commitment = get("commitment", "Commitment")
    warnings = []
    if not text(type_value):
        warnings.append("Choose Type: Event or Task.")
    if task:
        if event_kind: warnings.append("Event kind is ignored for a task; clear it in Notion.")
        if commitment: warnings.append("Commitment is ignored for a task; use Completed for progress.")
    elif task_kind:
        warnings.append("Task kind is ignored for an event; clear it in Notion.")
    return {"is_deadline": task, "task_kind": task_kind if task else None,
            "event_kind": event_kind if not task else None,
            "commitment": None if task else commitment, "setup_warnings": warnings}


def validate_changes(type_value, changes, source=None):
    task = is_task(type_value, source)
    invalid = ("event_kind", "commitment") if task else ("task_kind",)
    for key in invalid:
        if changes.get(key):
            label = "tasks" if task else "events"
            raise ValueError(f"{key.replace('_', ' ').capitalize()} does not apply to {label}. Clear it or choose the matching Type.")
