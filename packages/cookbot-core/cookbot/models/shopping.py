from pydantic import BaseModel


class ShoppingItem(BaseModel):
    name: str
    quantity: str   # e.g. "400g", "3 szt.", "2 łyżki"
    section: str    # "warzywa/owoce" | "nabiał" | "mięso/ryby" | "piekarnia" | "suche produkty" | "inne"
    # Pantry tag (STEP 51), set by models/pantry_math.subtract_pantry — NEVER by the
    # ShoppingListAgent, which knows nothing about a pantry. A real field rather
    # than a suffix on `name`, so the copied text and the Frisco product lookup
    # both stay clean. Defaulted so pre-STEP-51 payloads still parse.
    pantry_note: str = ""


class ShoppingList(BaseModel):
    items: list[ShoppingItem]
    sections: list[str]  # ordered section names that are present
