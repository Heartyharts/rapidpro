import requests

from django.utils import timezone

from temba.request_logs.models import HTTPLog

from ...models import ClassifierType, Intent
from .views import ConnectView


class WitType(ClassifierType):
    """
    Type for classifiers from Wit.ai
    """

    CONFIG_ACCESS_TOKEN = "access_token"
    CONFIG_APP_ID = "app_id"

    name = "Wit.ai"
    slug = "wit"
    icon = "icon-wit"

    connect_view = ConnectView
    connect_blurb = """
        <a href="https://wit.ai">Wit.ai</a> is a Facebook owned natural language platform that supports up to 132 languages.
        The service is free and easy to use and a great choice for small to medium sized bots.
        """

    form_blurb = """
        You can find the parameters below on your Wit.ai console under your App settings.
        """

    INTENT_URL = "https://api.wit.ai/entities/intent"

    def get_active_intents_from_api(self, classifier):
        """
        Gets the current intents defined by this app. In Wit intents are treated as a special case of an entity. We
        fetch the possible values for that entity.
        """
        access_token = classifier.config[self.CONFIG_ACCESS_TOKEN]

        start = timezone.now()
        try:
            response = requests.get(self.INTENT_URL, headers={"Authorization": f"Bearer {access_token}"})
            elapsed = (timezone.now() - start).total_seconds() * 1000

            HTTPLog.create_from_response(
                HTTPLog.INTENTS_SYNCED, self.INTENT_URL, response, classifier=classifier, request_time=elapsed
            )

            response.raise_for_status()
            response_json = response.json()
        except requests.RequestException as e:
            HTTPLog.create_from_exception(HTTPLog.INTENTS_SYNCED, self.INTENT_URL, e, start, classifier=classifier)
            return []

        intents = []
        for intent in response_json["values"]:
            intents.append(Intent(name=intent["value"], external_id=intent["value"]))

        return intents
