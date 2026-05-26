from dataclasses import dataclass, field


@dataclass
class HitlLabels:
    heading: str        # "Runda {round}: Czy to wygląda dobrze?"
    approve: str        # "Zatwierdź"
    modify: str         # "Zmień"
    reject: str         # "Odrzuć"
    modify_placeholder: str  # "Co zmienić?"
    modify_send: str    # "Wyślij"
    approved_note: str  # "Zatwierdzono"
    rejected_note: str  # "Odrzucono"
    modification_note: str   # "Zmiana: \"{text}\""


@dataclass
class UiStrings:
    greeting: str
    thinking: str
    summary_prefix: str  # uses {dish}, {time}, {items}
    searching: str
    max_rounds_reached: str
    intake_questions: list[str]
    hitl: HitlLabels


_PL = UiStrings(
    greeting="Cześć! Jestem Twoim asystentem kulinarnym. Odpowiedz na kilka pytań, a znajdę Ci idealny przepis!",
    thinking="Rozumiem! Chwila, zaraz coś wymyślę…",
    summary_prefix="Świetnie! Danie: {dish}, czas: {time} min, składniki: {items}. Szukam przepisu…",
    searching="Szukam przepisu…",
    max_rounds_reached="Osiągnięto maksymalną liczbę rund dopracowania — używam najlepszej wersji.",
    intake_questions=[
        "Co chcesz dzisiaj ugotować? (np. pasta, zupa, sałatka — lub powiedz 'zaproponuj coś' / 'zaproponuj na podstawie moich składników')",
        "Ile porcji potrzebujesz? (np. 1, 2, 4 — lub 'tylko dla mnie')",
        "Ile masz czasu? (np. 20 minut, 1 godzina — lub 'bez pośpiechu')",
        "Czy masz jakieś składniki, które chcesz wykorzystać? Wymień je lub powiedz 'nie'.",
        "Coś jeszcze do uwzględnienia? (np. 'łatwe do odgrzania w biurze', 'bez ostrego', 'dla dzieci') — lub powiedz 'nie'.",
    ],
    hitl=HitlLabels(
        heading="Runda {round}: Czy to wygląda dobrze?",
        approve="Zatwierdź",
        modify="Zmień",
        reject="Odrzuć",
        modify_placeholder="Co zmienić?",
        modify_send="Wyślij",
        approved_note="Zatwierdzono",
        rejected_note="Odrzucono",
        modification_note='Zmiana: "{text}"',
    ),
)

_EN = UiStrings(
    greeting="Hi! I'm your cooking helper. Answer a few quick questions and I'll find you the perfect recipe!",
    thinking="Got it! Let me work out what we can make…",
    summary_prefix="Understood! Dish: {dish}, time: {time} min, using: {items}. Searching for a recipe now…",
    searching="Searching for a recipe now…",
    max_rounds_reached="Maximum refinement rounds reached — using best version.",
    intake_questions=[
        "What do you want to cook today? (e.g. pasta, soup, salad — or say 'surprise me' / 'suggest based on my ingredients')",
        "How many portions do you need? (e.g. 1, 2, 4 — or 'just me')",
        "How much time do you have? (e.g. 20 minutes, 1 hour, or 'no rush')",
        "Do you have any ingredients you'd like to use? List them, or say 'no' to skip.",
        "Anything else to keep in mind? (e.g. 'easy to reheat at the office', 'no spicy food', 'kid-friendly') — or say 'no'.",
    ],
    hitl=HitlLabels(
        heading="Round {round}: Does this look good?",
        approve="Approve",
        modify="Modify",
        reject="Reject",
        modify_placeholder="What should be changed?",
        modify_send="Send",
        approved_note="Approved",
        rejected_note="Rejected",
        modification_note='Modification: "{text}"',
    ),
)

_STRINGS: dict[str, UiStrings] = {"pl": _PL, "en": _EN}


def ui_strings_for(language: str) -> UiStrings:
    return _STRINGS.get(language, _PL)
