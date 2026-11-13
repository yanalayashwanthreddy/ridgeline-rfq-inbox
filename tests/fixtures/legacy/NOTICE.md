# Legacy-format fixtures

These files are unmodified copies from the **Apache POI** project's test data
(https://github.com/apache/poi/tree/trunk/test-data), licensed under the Apache License 2.0
(https://www.apache.org/licenses/LICENSE-2.0). They are real files written by Microsoft Word
and Outlook, used here only as test input for `rfq/legacy.py`.

| file | from POI | what it exercises |
|---|---|---|
| `attachment_test_msg.msg` | `test-data/hsmf/` | Outlook message, 2 attachments (a Unicode `.doc` and a `.txt`), accented subject |
| `attachment_msg_pdf.msg` | `test-data/hsmf/` | an Outlook message embedded in another, plus a PDF; sender address only in transport headers |
| `quick.msg` | `test-data/hsmf/` | 8-bit (ANSI) strings; sender stored as an Exchange X.500 name |
| `SampleDoc.doc` | `test-data/document/` | Word 97-2003 text, curly quotes |
| `simple-table.doc` | `test-data/document/` | Word 97 table (2 rows x 3 columns) |

The expected values in `tests/test_legacy.py` come from two independent tools run on these
same files: **antiword** (for `.doc`) and **extract-msg** (for `.msg`). They were not taken
from this project's own reader.
