from django import forms
from django.conf import settings
from django.contrib.auth.forms import UserCreationForm


class SignupForm(UserCreationForm):
    first_name = forms.CharField(
        label="Nome visualizzato",
        max_length=30,
        required=False,
        help_text="Come ti vedranno gli altri (es. Manuel). Se vuoto si usa lo username.",
    )
    invite_code = forms.CharField(label="Codice d'invito", max_length=100)

    class Meta(UserCreationForm.Meta):
        fields = ("username", "first_name")

    def __init__(self, *args, require_code=True, **kwargs):
        super().__init__(*args, **kwargs)
        # Niente codice se non è configurato o se si arriva dal link d'invito di un gruppo
        if not settings.SIGNUP_CODE or not require_code:
            del self.fields["invite_code"]

    def clean_invite_code(self):
        code = self.cleaned_data["invite_code"].strip()
        if code != settings.SIGNUP_CODE:
            raise forms.ValidationError("Codice d'invito non valido.")
        return code
