from pydantic import BaseModel


class ShoppingItem(BaseModel):
    name: str
    quantity: str   # e.g. "400g", "3 szt.", "2 łyżki"
    section: str    # "warzywa/owoce" | "nabiał" | "mięso/ryby" | "piekarnia" | "suche produkty" | "inne"


class ShoppingList(BaseModel):
    items: list[ShoppingItem]
    sections: list[str]  # ordered section names that are present
