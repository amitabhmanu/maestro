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


def _ctx(store, text):
    from maestro.crawl.fetcher import Page
    from maestro.crawl.runner import ExtractContext

    raw = store.record_raw_page("u", 200, "x", "p")
    return ExtractContext(store, {"purpose": "spine"}, Page("u", 200, text.encode(), "x", None), raw)


DUO = """<meta property="og:title" content="{who}" /><section class="recording-section"><table>
<tr class="issued"><td data-label="Company">Columbia (U.K.)</td><td data-label="Matrix No.">WL256</td>
 <td data-label="First Recording Date">12/14/1925</td><td data-label="Primary Title">Charleston blues</td>
 <td data-label="Primary Performer">Clément Doucet ; Jean Wiener</td><td data-label="Role">{roles}</td></tr>
<tr class="issued"><td data-label="Company">Brunswick</td><td data-label="Matrix No.">E30325</td>
 <td data-label="First Recording Date">7/5/1929</td><td data-label="Primary Title">Blues</td>
 <td data-label="Primary Performer">Rosita Renard</td><td data-label="Role">composer</td></tr>
</table></section>"""


def test_work_canon_splits_performances_from_works_and_reads_partner_pages(store_for):
    s = store_for("jean-wiener")
    dahr.extract(_ctx(s, DUO.format(who="Jean Wiener", roles="instrumentalist, piano, composer")))
    rows = s.query_graph(
        """SELECT e.label, c.value #>> '{}' AS kind FROM entity e
           LEFT JOIN claim c ON c.entity_id = e.id AND c.field = 'work_kind' WHERE e.type = 'work' ORDER BY 1, 2""")
    # The duo master is a performance; "Charleston blues" and "Blues" are also works he wrote.
    assert [(r["label"], r["kind"]) for r in rows] == [
        ("Blues", None), ("Charleston blues", "performance_recording"), ("Charleston blues", None)]
    n = s.query_graph("SELECT count(*) AS n FROM entity WHERE type = 'work'")[0]["n"]
    assert n == 3
    # Doucet's page: the Renard master is not Wiener's; the duo master is the same unit as before.
    dahr.extract(_ctx(s, DUO.format(who="Clément Doucet", roles="instrumentalist, piano, composer")))
    assert s.query_graph("SELECT count(*) AS n FROM entity WHERE type = 'work'")[0]["n"] == 3
