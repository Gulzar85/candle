import re

from django import template

register = template.Library()

_HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


@register.filter(name="safe_hex_color")
def safe_hex_color(value: str, fallback: str = "#52525b") -> str:
    """Return ``value`` if it's a strict ``#rrggbb`` hex color, else ``fallback``.

    Used wherever a user-controlled color (``Profile.accent_color``) is
    interpolated into a ``<style>`` block rather than an HTML attribute --
    autoescaping protects HTML context but does nothing for CSS syntax
    characters like ``;`` or ``}``, so this is the injection guard for that
    context.
    """
    if isinstance(value, str) and _HEX_COLOR_RE.match(value):
        return value
    return fallback


# Shared design-system input classes so every auth/account field is consistent.
INPUT_CLASS = (
    "w-full h-11 rounded-lg border bg-background px-3 py-2 text-sm "
    "placeholder:text-muted-foreground focus:outline-none focus:ring-2 "
    "focus:ring-ring focus:border-ring disabled:opacity-50 min-h-[44px]"
)
INPUT_CLASS_ERROR = INPUT_CLASS + " border-danger"
INPUT_CLASS_OK = INPUT_CLASS + " border-input"


@register.filter(name="inputclass")
def inputclass(field, extra: str = ""):
    """Render a BoundField's widget with design-system classes.

    The widget's own attributes (autocomplete, etc.) are preserved; only the
    ``class`` attribute is set. ``extra`` is appended to the base classes and
    the error variant is chosen automatically when the field has errors.
    """
    if not hasattr(field, "as_widget"):
        return field
    css = INPUT_CLASS_ERROR if field.errors else INPUT_CLASS_OK
    if extra:
        css = css + " " + extra
    widget = field.field.widget
    attrs = dict(widget.attrs)
    attrs["class"] = css
    return field.as_widget(widget=widget, attrs=attrs)


@register.filter(name="inputclass_alpine")
def inputclass_alpine(field, alpine_attrs: str = ""):
    """Render a BoundField's widget inside an Alpine component.

    ``alpine_attrs`` is a single ``name=value`` Alpine binding injected
    verbatim onto the widget (e.g. ``":type=show ? 'text' : 'password'"``).
    Split only on the first ``=`` -- the value itself commonly contains
    spaces (a ternary expression), so splitting on whitespace first would
    shred it into several bogus attributes. Preserves the existing widget
    classes and adds ``pr-10`` so text clears an inline toggle.
    """
    if not hasattr(field, "as_widget"):
        return field
    css = (INPUT_CLASS_ERROR if field.errors else INPUT_CLASS_OK) + " pr-10"
    widget = field.field.widget
    attrs = dict(widget.attrs)
    attrs["class"] = css
    if alpine_attrs:
        name, _, value = alpine_attrs.partition("=")
        attrs[name.strip()] = value
    return field.as_widget(widget=widget, attrs=attrs)


@register.filter(name="addclass", needs_autoescape=True)
def addclass(value, css_class: str, autoescape: bool = True):
    """Re-render a BoundField's widget adding a CSS class to existing attrs."""
    if not hasattr(value, "as_widget"):
        return value
    widget = value.field.widget
    attrs = dict(widget.attrs)
    existing = attrs.get("class", "")
    attrs["class"] = (existing + " " + css_class).strip()
    return value.as_widget(widget=widget, attrs=attrs)
