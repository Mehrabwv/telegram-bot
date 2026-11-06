
import httpx

from config import GROQ_API_KEY

MODELS_URL = (
    "https://api.groq.com/openai/v1/models"
)


def main():

    if not GROQ_API_KEY:
        print("ERROR: GROQ_API_KEY is missing.")
        print("Check your .env file.")
        return

    print("Connecting to Groq...")
    print("Retrieving available models...\n")

    try:
        response = httpx.get(
            MODELS_URL,
            headers={
                "Authorization": (
                    f"Bearer {GROQ_API_KEY}"
                )
            },
            timeout=30
        )

        response.raise_for_status()

        data = response.json()

        models = data.get("data", [])

        models = sorted(
            models,
            key=lambda model: model.get("id", "")
        )

        print("=" * 45)
        print("AVAILABLE GROQ MODELS")
        print("=" * 45)

        for index, model in enumerate(models, 1):

            model_id = model.get(
                "id", "Unknown"
            )

            print(f"{index}. {model_id}")

        print("\nTotal models:", len(models))
        print(
            "Note: Not all models necessarily "
            "have free-tier access."
        )

    except httpx.HTTPStatusError as error:

        print(
            "Groq API returned HTTP",
            error.response.status_code
        )

    except httpx.RequestError:

        print(
            "Could not connect to Groq. "
            "Check your internet connection."
        )

    except ValueError:

        print("Invalid JSON response from Groq.")


if __name__ == "__main__":
    main()
