"""Input validation utilities shared across skill tools."""

VALID_PERM_TYPES = {"full_access", "edit", "view", "comment"}
VALID_FILE_TYPES = {"docx", "sheet", "bitable", "folder", "file", "mindnote", "slides"}
VALID_LINK_SHARE = {"tenant_readable", "tenant_editable", "anyone_readable", "anyone_editable", "closed"}
VALID_MEMBER_TYPES = {"openid", "chatid", "userid", "departmentid"}


def validate_required(params: dict, required_fields: list[str]) -> None:
    """Raise ValueError if any required field is missing or empty."""
    missing = [f for f in required_fields if not params.get(f)]
    if missing:
        raise ValueError(f"Missing required fields: {', '.join(missing)}")


def validate_enum(value: str, valid_set: set, field_name: str) -> None:
    """Raise ValueError if value not in valid_set."""
    if value not in valid_set:
        raise ValueError(f"Invalid {field_name}: '{value}'. Must be one of: {', '.join(sorted(valid_set))}")


def validate_action(action: str, valid_actions: set, skill_name: str) -> None:
    """Validate action name. Raise ValueError with help message."""
    if action not in valid_actions:
        raise ValueError(f"Unknown {skill_name} action: '{action}'. Valid actions: {', '.join(sorted(valid_actions))}")
