import hashlib

import pytest

from app import import_sources as source


def read(value, delimiter=";"):
    return source.read_csv(value.encode("utf-8"), delimiter=delimiter)


def test_csv_preserves_quoted_values_unicode_multiline_and_source_line_numbers():
    data = '\ufeffIdentity;Name;Note\r\nRWT_0007_WOOD;"Dub; světlý";"Two\r\nlines"\r\nRWT_0008_WOOD;"Quoted ""oak""";\r\n'
    table = read(data)
    assert table.headers == ("Identity", "Name", "Note")
    assert table.rows[0].values == ("RWT_0007_WOOD", "Dub; světlý", "Two\r\nlines")
    assert table.rows[1].values == ("RWT_0008_WOOD", 'Quoted "oak"', "")
    assert [row.number for row in table.rows] == [2, 4]
    assert table.source_sha256 == hashlib.sha256(data.encode("utf-8")).hexdigest()
    assert "Dub" not in repr(table) and "Dub" not in repr(table.rows[0])


def test_csv_requires_explicit_delimiter_and_normalizes_only_headers():
    table = read("Identita, Na\u0301zev \nRWT_0001_WOOD,  Dub  ", delimiter=",")
    assert table.headers == ("Identita", "Název")
    assert table.rows[0].values[1] == "  Dub  "


@pytest.mark.parametrize(("value", "code"), [
    ("", "IMPORT_FILE_SIZE"), ("Identity;Name\n", "IMPORT_EMPTY_TABLE"),
    ("Identity;\nA;B", "IMPORT_HEADER"), ("Name; name \nA;B", "IMPORT_DUPLICATE_HEADER"),
    ("Na\u0301zev;Název\nA;B", "IMPORT_DUPLICATE_HEADER"),
    ("Identity;Name\nA;B;C", "IMPORT_ROW_WIDTH"), ("Identity;Name\nA", "IMPORT_ROW_WIDTH"),
    ('Identity;Name\nA;"unterminated', "IMPORT_CSV_INVALID"),
    ("Identity;Name\nA;bad\x00value", "IMPORT_CELL_CONTROL"),
    ("Identity;Name\nA;bad\u202evalue", "IMPORT_CELL_CONTROL"),
    ("Identity;Name\nA;" + "x" * 2049, "IMPORT_CELL_SIZE"),
])
def test_csv_errors_are_safe(value, code):
    with pytest.raises(source.ImportSourceError) as caught:
        read(value)
    assert str(caught.value) == code


@pytest.mark.parametrize("prefix", ["=", "+", "-", "@", " \t="])
def test_formula_like_cells_are_never_accepted_or_reflected(prefix):
    with pytest.raises(source.ImportSourceError) as caught:
        read("Identity;Name\nRWT_0001_WOOD;" + prefix + "PRIVATE_SYNTHETIC_VALUE")
    assert (caught.value.code, caught.value.row, caught.value.column) == ("IMPORT_FORMULA", 2, 2)
    assert "PRIVATE_SYNTHETIC_VALUE" not in str(caught.value)


def test_file_encoding_and_delimiter_limits():
    for data, delimiter, code in [(b"\xff", ";", "IMPORT_UTF8_REQUIRED"),
                                  (b"A;B\nC;D", "\t", "IMPORT_DELIMITER"),
                                  (b"x" * (source.MAX_UPLOAD_BYTES + 1), ";", "IMPORT_FILE_SIZE")]:
        with pytest.raises(source.ImportSourceError, match=code):
            source.read_csv(data, delimiter=delimiter)


def test_row_and_column_limits_include_empty_rows(monkeypatch):
    monkeypatch.setattr(source, "MAX_ROWS", 2)
    assert len(read("A;B\n1;2\n3;4").rows) == 2
    with pytest.raises(source.ImportSourceError, match="IMPORT_ROW_LIMIT"):
        read("A;B\n1;2\n;\n3;4")
    with pytest.raises(source.ImportSourceError, match="IMPORT_COLUMN_LIMIT"):
        read(";".join(str(i) for i in range(33)) + "\n" + ";".join("x" for _ in range(33)))


def test_empty_rectangular_rows_are_omitted_without_renumbering():
    table = read("Identity;Name\n;\n\nRWT_0001_WOOD;Dub\n; ")
    assert len(table.rows) == 1 and table.rows[0].number == 4
