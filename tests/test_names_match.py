#!/usr/bin/env python3
"""
Due nomi sono la stessa persona? Il caso in cui sbagliare costa di piu'.

Perche' questo file esiste (1 set 2026)
---------------------------------------
`_names_match` decide se una nuova osservazione appartiene a un giocatore
gia' in anagrafica. Un falso positivo qui non produce una corroborazione
sbagliata: fonde le prove di due persone reali in un profilo solo, e
cancella dall'esistenza quella che perde.

La prima riga della funzione era `if a == b or a in b or b in a`, cioe'
contenimento di SOTTOSTRINGA, e annullava tutta la prudenza scritta sotto:

    "mora" sta dentro "dylan mora"  ->  match
    "mora" sta dentro "thiago mora" ->  match

Risultato misurato sul database vero: un record chiamato "Mora" — un
messicano del Club Tijuana — aveva assorbito Dylan Mora (Nacional) e
Thiago Mora (Boston River), due convocati diversi della stessa sub-17
uruguaiana. E stando su stringa invece che su parole prendeva anche
"morales" e "moraes", che non sono nemmeno lo stesso cognome.

Sei record a un token contenevano prove di piu' di una persona. Il
peggiore, "Felipe", ne teneva sei — compreso un comunicato di squalifica
("SANCIONADO LUIS FELIPE MARQUINEZ") — perche' Felipe e' un nome di
battesimo e la sottostringa pesca chiunque ce l'abbia in mezzo.

Il gate ha retto: tutti e sei erano `nome_singolo`, quindi publishable=0,
e in dashboard non e' mai arrivato niente. Ma i giocatori veri erano
spariti a monte, e quello nessun gate lo recupera.

    PYTHONIOENCODING=utf-8 python -m unittest tests.test_names_match -v
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.database_v2 import OB1DatabaseV2


class TestNamesMatch(unittest.TestCase):
    def setUp(self):
        # Senza __init__: la funzione non tocca il database.
        self.match = OB1DatabaseV2.__new__(OB1DatabaseV2)._names_match

    # --- non sono la stessa persona -------------------------------------
    def test_un_cognome_da_solo_non_cattura_chi_lo_porta(self):
        # I due casi veri: stesso cognome, stessa convocazione, persone diverse.
        self.assertFalse(self.match("Dylan Mora", "Mora"))
        self.assertFalse(self.match("Thiago Mora", "Mora"))

    def test_un_nome_di_battesimo_da_solo_non_cattura_nessuno(self):
        for n in ("Felipe De Leon", "Luis Felipe Marquinez",
                  "Felipe Morais", "Jhojan Felipe Zuniga Gomez"):
            self.assertFalse(self.match(n, "Felipe"), n)

    def test_sottostringa_che_non_e_una_parola(self):
        # "mora" sta dentro "morales" solo come sequenza di lettere.
        self.assertFalse(self.match("Mora", "Morales"))
        self.assertFalse(self.match("Mora", "Moraes"))

    def test_stesso_cognome_nomi_diversi(self):
        self.assertFalse(self.match("Luigi Saviolo", "Noah Saviolo"))

    def test_stesso_nome_cognomi_diversi(self):
        self.assertFalse(self.match("Josmar Galea", "Josmar Palacios"))

    def test_il_caso_storico_dei_due_juan_jose(self):
        # Il commento nel codice lo dava per risolto dal 2026; verificato il
        # 1 set, passava ancora: surname_candidates propone ['jose','camacho']
        # e quel 'jose' bastava. Ora il confronto e' sull'ULTIMO token.
        self.assertFalse(self.match("Juan Jose Fori Viveros",
                                    "Juan Jose Camacho"))
        self.assertFalse(self.match("Luis Eduardo Maturana Moreno",
                                    "Luis Eduardo Mena Padilla"))

    # --- sono la stessa persona: non devono spezzarsi -------------------
    def test_identici(self):
        self.assertTrue(self.match("Luis Machín", "Luis Machín"))

    def test_nome_corto_dentro_nome_completo(self):
        # La AUF scrive "Luis Machín", la sua scheda "Luis Eduardo Machín
        # Trindade": e' lui. Questo e' il motivo per cui il contenimento
        # esiste — va tenuto, ma per parole intere e da almeno due token.
        self.assertTrue(self.match("Luis Machin",
                                   "Luis Eduardo Machin Trindade"))
        self.assertTrue(self.match("Gabriel Da Silva",
                                   "Gabriel Da Silva Rodriguez"))

    def test_doppio_cognome_ispanico_con_un_nome_in_meno(self):
        self.assertTrue(self.match("Juan Carlos Fori Viveros",
                                   "Juan Fori Viveros"))

    def test_vuoti(self):
        self.assertFalse(self.match("", "Mora"))
        self.assertFalse(self.match("Mora", ""))


if __name__ == "__main__":
    unittest.main()
