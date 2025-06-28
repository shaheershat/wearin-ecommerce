from django import template
from django.template.defaultfilters import stringfilter # Important import

register = template.Library() # This line registers the library

@register.filter # This decorator registers the 'replace' function as a filter
@stringfilter
def replace(value, arg):
    """
    Replaces all occurrences of the first string with the second string.
    Usage: {{ value|replace:"old,new" }}
    """
    if len(arg.split(',')) != 2:
        return value
    old, new = arg.split(',')
    return value.replace(old, new)