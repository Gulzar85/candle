from django import template

register = template.Library()

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

    ``alpine_attrs`` is space-separated Alpine bindings injected verbatim onto
    the widget (e.g. ``":type=show ? 'text' : 'password'"``). Preserves the
    existing widget classes and adds ``pr-10`` so text clears an inline toggle.
    """
    if not hasattr(field, "as_widget"):
        return field
    css = (INPUT_CLASS_ERROR if field.errors else INPUT_CLASS_OK) + " pr-10"
    widget = field.field.widget
    attrs = dict(widget.attrs)
    attrs["class"] = css
    if alpine_attrs:
        for raw in alpine_attrs.split(" "):
            if not raw:
                continue
            name, _, value = raw.partition("=")
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
