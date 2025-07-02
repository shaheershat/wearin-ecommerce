from allauth.account.models import EmailAddress

def patched_emailaddress_str(self):
    return str(self.email)

EmailAddress.__str__ = patched_emailaddress_str
