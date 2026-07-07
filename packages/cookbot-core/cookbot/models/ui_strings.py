from dataclasses import dataclass


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
    # Spiżarnia UI labels
    spizarnia_heading: str = "Spiżarnia"
    spizarnia_empty: str = "Twoja spiżarnia jest pusta"
    spizarnia_toggle: str = "Użyj składników ze spiżarni"
    spizarnia_add_placeholder: str = "Dodaj składnik…"
    spizarnia_add_button: str = "Dodaj"
    shopping_list_heading: str = "Lista zakupów"
    shopping_list_clear: str = "Wyczyść zaznaczone"
    shopping_list_add_placeholder: str = "Dodaj pozycję…"
    shopping_list_add_button: str = "Dodaj"
    shopping_list_organize: str = "Poukładaj listę zakupów"
    shopping_list_organizing: str = "Układam…"
    shopping_list_copy: str = "Kopiuj"
    shopping_list_share: str = "Udostępnij"
    shopping_list_copied: str = "Skopiowano ✓"
    shopping_list_empty: str = "Lista jest pusta"
    frisco_button: str = "Znajdź w Frisco"
    frisco_heading: str = "Produkty w Frisco"
    frisco_not_found: str = "Nie znaleziono"
    frisco_open: str = "otwórz w Frisco"
    frisco_loading: str = "Szukam w Frisco…"
    frisco_generated_at: str = "Katalog z dnia"
    spizarnia_offer_add: str = "Dodać brakujące do listy zakupów?"
    spizarnia_offer_remove: str = "Usunąć zużyte składniki ze spiżarni?"
    spizarnia_offer_confirm: str = "Tak"
    spizarnia_offer_skip: str = "Nie"
    login_heading: str = "Zaloguj się"
    login_email: str = "E-mail"
    login_password: str = "Hasło"
    login_button: str = "Zaloguj się"
    logout_button: str = "Wyloguj"
    # Quota — {resets} is a human date/time of when the window resets.
    quota_daily_reached: str = "Wykorzystałeś dzienny limit tokenów. Odnowi się {resets}."
    quota_monthly_reached: str = "Wykorzystałeś miesięczny limit tokenów. Odnowi się {resets}."
    quota_disabled: str = "Twoje konto zostało wyłączone. Skontaktuj się z administratorem."


_PL = UiStrings(
    greeting=(
        "Cześć! Jestem Twoim asystentem kulinarnym. Napisz co chcesz ugotować, a znajdę Ci przepis "
        "— mogę też dodać danie do kalendarza lub przygotować listę zakupów."
    ),
    thinking="Rozumiem! Chwila, zaraz coś wymyślę…",
    summary_prefix="Świetnie! Danie: {dish}, czas: {time} min, składniki: {items}. Szukam przepisu…",
    searching="Szukam przepisu…",
    max_rounds_reached="Osiągnięto maksymalną liczbę rund dopracowania — używam najlepszej wersji.",
    intake_questions=[
        "Co chcesz dzisiaj ugotować? (np. makaron, zupa, sałatka "
        "— lub powiedz 'zaproponuj coś' / 'zaproponuj na podstawie moich składników')",
        "Ile porcji potrzebujesz? (np. 1, 2, 4 — lub 'tylko dla mnie')",
        "Ile masz czasu? (np. 20 minut, 1 godzina — lub 'bez pośpiechu')",
        "Czy masz jakieś składniki, które chcesz wykorzystać? Wymień je lub powiedz 'nie'.",
        "Coś jeszcze do uwzględnienia? (np. 'łatwe do odgrzania w biurze', 'bez ostrego', 'dla dzieci') "
        "— lub powiedz 'nie'.",
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
    greeting=(
        "Hi! I'm your cooking assistant. Tell me what you'd like to cook and I'll find a recipe "
        "— I can also add meals to your calendar or build a shopping list."
    ),
    thinking="Got it! Let me work out what we can make…",
    summary_prefix="Understood! Dish: {dish}, time: {time} min, using: {items}. Searching for a recipe now…",
    searching="Searching for a recipe now…",
    max_rounds_reached="Maximum refinement rounds reached — using best version.",
    intake_questions=[
        "What do you want to cook today? (e.g. pasta, soup, salad "
        "— or say 'surprise me' / 'suggest based on my ingredients')",
        "How many portions do you need? (e.g. 1, 2, 4 — or 'just me')",
        "How much time do you have? (e.g. 20 minutes, 1 hour, or 'no rush')",
        "Do you have any ingredients you'd like to use? List them, or say 'no' to skip.",
        "Anything else to keep in mind? (e.g. 'easy to reheat at the office', 'no spicy food', 'kid-friendly') "
        "— or say 'no'.",
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
    spizarnia_heading="Pantry",
    spizarnia_empty="Your pantry is empty",
    spizarnia_toggle="Use pantry ingredients",
    spizarnia_add_placeholder="Add ingredient…",
    spizarnia_add_button="Add",
    shopping_list_heading="Shopping list",
    shopping_list_clear="Clear checked",
    shopping_list_add_placeholder="Add an item…",
    shopping_list_add_button="Add",
    shopping_list_organize="Organize shopping list",
    shopping_list_organizing="Organizing…",
    shopping_list_copy="Copy",
    shopping_list_share="Share",
    shopping_list_copied="Copied ✓",
    shopping_list_empty="The list is empty",
    frisco_button="Find on Frisco",
    frisco_heading="Products on Frisco",
    frisco_not_found="Not found",
    frisco_open="open on Frisco",
    frisco_loading="Searching Frisco…",
    frisco_generated_at="Catalogue from",
    spizarnia_offer_add="Add missing ingredients to shopping list?",
    spizarnia_offer_remove="Remove used items from pantry?",
    spizarnia_offer_confirm="Yes",
    spizarnia_offer_skip="No",
    login_heading="Sign in",
    login_email="Email",
    login_password="Password",
    login_button="Sign in",
    logout_button="Sign out",
    quota_daily_reached="You've used your daily token limit. It resets {resets}.",
    quota_monthly_reached="You've used your monthly token limit. It resets {resets}.",
    quota_disabled="Your account has been disabled. Please contact an administrator.",
)

_STRINGS: dict[str, UiStrings] = {"pl": _PL, "en": _EN}


def ui_strings_for(language: str) -> UiStrings:
    return _STRINGS.get(language, _PL)
