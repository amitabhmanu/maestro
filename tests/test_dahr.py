"""Core DAHR extractor (saved name pages) and matching by matrix number."""

from maestro.extractors import dahr
from maestro.resolve import Groups, matrix_key, resolve

PAGE = """<section class="recording-section"><table class="detail-data">
<tr><th>Company</th><th>Matrix No.</th></tr>
<tr class="issued"><td data-label="Company">Victor</td>
 <td data-label="Matrix No."><a href="/index.php/matrix/refer/300017709">E0VB-4267</a></td>
 <td class="no-wrap size">10-in.</td><td data-label="First Recording Date">4/26/1950</td>
 <td data-label="Primary Title">A prison without walls</td><td data-label="Primary Performer">Eddy Arnold</td>
 <td data-label="Description.">Male vocal solo, with string band</td>
 <td data-label="Role">vocalist, leader, instrumentalist, guitar</td><td data-label="Audio">&nbsp;</td></tr>
<tr><td data-label="Company">Victor</td>
 <td data-label="Matrix No."><a href="/x">E1VB-1397</a></td><td data-label="First Recording Date">3/13/1951</td>
 <td data-label="Primary Title">I&#039;ll hold you in my heart</td><td data-label="Primary Performer">Eddie Fisher</td>
 <td data-label="Role">songwriter</td></tr>
<tr class="audio-available"><td data-label="Company">Victor</td>
 <td data-label="Matrix No."><a href="/y">D5-AB-0001</a></td><td data-label="First Recording Date">1945</td>
 <td data-label="Primary Title">Mommy please stay home with me</td><td data-label="Role">vocalist</td></tr>
</table></section>"""


def test_parse_rows():
    rows = dahr.parse(PAGE)
    assert [(r["matrix"], r["issued"], r["date"], r["year"]) for r in rows] == [
        ("E0VB-4267", True, "1950-04-26", 1950), ("E1VB-1397", False, "1951-03-13", 1951),
        ("D5-AB-0001", True, None, 1945)]
    assert rows[1]["title"] == "I'll hold you in my heart" and rows[1]["roles"] == ["songwriter"]


def test_matrix_key():
    assert matrix_key("E0-VB-4267") == matrix_key("E0VB-4267") == "E0VB4267"
    assert matrix_key("D4AB-0519") == matrix_key("D4-AB-519") == "D4AB519"
    assert matrix_key("E0-VB-5840-2") is None  # a remake take


def test_same_matrix_merges_across_sources(store_for):
    s = store_for("eddy-arnold")
    me = s.maestro_entity()

    def rec(source, xid, title, matrix):
        r = s.find_or_create_by_xref("recording", source, xid, title)
        s.upsert_claim(r, "matrix", value=matrix, source_id=source)
        s.upsert_claim(r, "credit", ref_entity_id=me, value={"role": "singer"}, source_id=source)
        return r

    pf = rec("praguefrank", "seq:1", "A PRISON WITHOUT WALLS", "E0-VB-4267")
    dh = rec("dahr", "matrix:Victor:E0VB-4267", "A prison without walls", "E0VB-4267")
    other = rec("dahr", "matrix:Victor:E0VB-4268", "Cuddle buggin' baby", "E0VB-4268")
    resolve(s)
    g = Groups(s)
    assert g.find(pf) == g.find(dh) and g.find(other) == other
