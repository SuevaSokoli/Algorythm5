"""Generate the synthetic sample exports. All people, emails and numbers are invented.

Run:  python tests/fixtures/build_samples.py
Writes sample_customers_messy.csv (Windows-1252, like old systems) and .xlsx next to this file.
"""

from __future__ import annotations

import csv
import io
from datetime import datetime
from pathlib import Path

from openpyxl import Workbook

HERE = Path(__file__).resolve().parent
TITLE_ROW = ["Eksport klientësh – sistemi i vjetër", "", "", "", "", "", "", "", ""]
HEADERS = [
    "Emri",
    "Mbiemri",
    "E-mail",
    "Tel",
    "Celular",
    "Qyteti",
    "Datëlindja",
    "Abonuar",
    "Shënime",
]
ROWS: list[list[object]] = [
    ["ANA", "HOXHA", "ANA.HOXHA@GMIAL.COM", "069 234 5678", "", "TIRANË", "12.03.1985", "po", ""],
    [
        "Ana",
        "Hoxha",
        "ana.hoxha@gmail.com",
        "",
        "+355 69 234 5678",
        "Tiranë",
        "1985-03-12",
        "",
        "VIP",
    ],
    [
        "DR. ËRMIRA",
        "ÇELA",
        "ermira.cela@yahoo.con",
        "04 223 4567 ext 12",
        "",
        "DURRËS",
        "5/6/90",
        "po",
        "",
    ],
    [
        "xhemal",
        "mcdonald",
        "Xhemal McDonald <xhemal.mcd@hotmial.com>",
        "",
        "0682345671",
        "shkodër",
        "",
        "po",
        "",
    ],
    [
        "Patrick",
        "O'BRIEN",
        "pat.obrien@outlook.com; patrick@obrien-family.ie",
        "",
        "00353 87 123 4567",
        "Dublin",
        "17 mars 1979",
        "yes",
        "",
    ],
    [
        "GJERGJ",
        "MACEDO",
        "gjergj.macedo@gmaul.com",
        "",
        "3.55692E+11",
        "Vlorë",
        "31.02.1988",
        "po",
        "",
    ],
    ["BESNIK", "KOLA", "", "", "067 345 6789", "Fier", "1990", "", ""],
    ["Drita", "Leka", "drita.leka@gmail.com", "", "069 876 5432", "Korçë", "03/04/1992", "jo", ""],
    [
        "Arben",
        "Krasniqi",
        "arben.k@gmail.com",
        "",
        "044 123 456",
        "Prishtinë",
        "",
        "po",
        "Ju lutem mos më dërgoni email",
    ],
    ["N/A", "N/A", "test@test.com", "000000", "", "-", "", "", ""],
    [
        "Klea",
        "Muça",
        "klea.muca@gmail.com",
        "",
        "0691112233 / 0687778899",
        "Berat",
        "44927",
        "po",
        "",
    ],
    ["Klea", "Muca", "klea.muca@gmail.com", "", "+355691112233", "BERAT", "", "", ""],
    ["Elira", "Dushku", "elira (at) gmail (dot) com", "", "", "Elbasan", "25/12/1995", "po", ""],
    [
        "Mirela",
        "Hoxha",
        "mirela.hoxha@gmail.com",
        "",
        "0692345678",
        "Tiranë",
        "",
        "po",
        "motra e Anës",
    ],
    ["Luan", "Berisha", "info@berisha-shpk.al", "04 245 6789", "", "Tiranë", "", "po", ""],
    ["Gj?rgji", "Prenga", "gjergji.prenga@gmail.com", "", "069 444 5566", "Lezhë", "", "", ""],
    ["Anila", "Brahimi", "anila.brahimi@gmail", "", "", "Kukës", "2030-01-01", "po", ""],
    [
        "Teuta",
        "Shehu",
        "teuta.shehu@gmail.com",
        "",
        "069 555 6677",
        "Gjirokastër",
        "",
        "unsubscribed",
        "",
    ],
    ["Sokol", "Dervishi", "sokol.dervishi@gmail.com", "", "0693334455", "Sarandë", "", "Po", ""],
    ["Sokol", "Dervishaj", "s.dervishaj@yahoo.com", "", "0699990000", "SARANDË", "", "po", ""],
    ["", "", "", "", "", "", "", "", ""],
]


def build_csv() -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";", lineterminator="\r\n")
    writer.writerow(TITLE_ROW)
    writer.writerow(HEADERS)
    for row in ROWS:
        writer.writerow(row)
    return buffer.getvalue().encode("cp1252")


def build_xlsx() -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Klientet"
    sheet.append(HEADERS)
    for row in ROWS:
        values = list(row)
        # Excel-style damage: numeric phones (lost leading zero) and real date cells.
        if values[4] == "0682345671":
            values[4] = 682345671
        if values[6] == "1985-03-12":
            values[6] = datetime(1985, 3, 12)
        sheet.append(values)
    workbook.properties.created = datetime(2000, 1, 1)
    workbook.properties.modified = datetime(2000, 1, 1)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def main() -> None:
    (HERE / "sample_customers_messy.csv").write_bytes(build_csv())
    (HERE / "sample_customers_messy.xlsx").write_bytes(build_xlsx())


if __name__ == "__main__":
    main()
