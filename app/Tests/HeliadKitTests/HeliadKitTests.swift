import Foundation
import XCTest
@testable import HeliadKit

final class ModelTests: XCTestCase {
    func decode<T: Decodable>(_ type: T.Type, _ json: String) throws -> T {
        try HeliadJSON.decoder().decode(T.self, from: Data(json.utf8))
    }

    func testJob() throws {
        let job = try decode(Job.self, """
        {"job_id":1,"source":"fido","status":"failed","phase":null,"files_total":5,"files_done":0,
         "error":"stored 0 of 5 files; timeout","created_at":"2026-10-18T21:34:24+00:00",
         "updated_at":"2026-10-18T21:37:30+00:00",
         "query":{"end":"2025-09-21T16:05:00","instrument":"AIA","sample_s":60.0,"start":"2025-09-21T16:00:00","wavelength":193.0},
         "result":{"found":5,"fetched":0,"stored":0,"errors":["timeout"],"ingest":{}}}
        """)
        XCTAssertEqual(job.title, "AIA · 193 Å · 2025-09-21T16:00:00 to 2025-09-21T16:05:00")
        XCTAssertEqual(job.summary, "stored 0 of 5")
        // Keys inside an open dictionary stay as the core wrote them (no snake_case conversion).
        // (A whole-number float such as 60.0 decodes as .int; doubleValue reads either.)
        XCTAssertEqual(job.query["sample_s"]?.doubleValue, 60)
        XCTAssertTrue(job.canRetry)
        XCTAssertFalse(job.isActive)
    }

    func testDatasetAndFile() throws {
        let datasets = try decode([Dataset].self, """
        [{"id":1,"dataset_key":"sdo/aia/-/-/1/193","observatory":"SDO","instrument":"AIA","detector":null,
          "product":null,"level":"1","wavelength":193.0,"label":"SDO AIA 193 Å L1","observations":5,
          "first":"2025-09-21T16:00:04.840","last":"2025-09-21T16:04:04.840","bytes":60065280}]
        """)
        XCTAssertEqual(datasets[0].datasetKey, "sdo/aia/-/-/1/193")
        XCTAssertEqual(datasets[0].bytes, 60_065_280)
        let file = try decode(LibraryFile.self, """
        {"file_id":3,"relpath":"store/sdo/aia/1/193/2025/09/21/aia.fits","sha256":"ab12cd","size":12012480,
         "version":null,"observation_id":3,"date_obs":"2025-09-21T16:02:04.840","dataset_id":1,
         "dataset_key":"sdo/aia/-/-/1/193","label":"SDO AIA 193 Å L1","observatory":"SDO","instrument":"AIA",
         "detector":null,"product":null,"level":"1","wavelength":193.0}
        """)
        XCTAssertEqual(file.name, "aia.fits")
        XCTAssertEqual(file.thumbnailURL(libraryRoot: "/lib").path, "/lib/.thumbs/ab/ab12cd.png")
    }

    func testFidoAndJsocSearchResults() throws {
        let fido = try decode(SearchResult.self, """
        {"query":{},"total":2,"providers":[{"client":"VSOClient","count":2,"columns":["Start Time","Size"],
         "rows":[{"Start Time":"2025-09-21T16:00:04.000","Size":"64.64844 Mibyte"},{"Start Time":"x","Size":"y"}]}]}
        """)
        XCTAssertEqual(fido.providers.map(\.client), ["VSOClient"])
        XCTAssertEqual(fido.providers[0].rows[0]["Size"]?.display, "64.64844 Mibyte")
        let jsoc = try decode(SearchResult.self, """
        {"query":{},"recordset":"aia.lev1_euv_12s[2025-09-21T16:00:00Z/300s@60s][193]","total":1,
         "rows":[{"T_REC":"2025-09-21T16:00:00Z","WAVELNTH":"193","QUALITY":"0"}]}
        """)
        XCTAssertEqual(jsoc.providers[0].columns, ["QUALITY", "T_REC", "WAVELNTH"])
        XCTAssertNotNil(jsoc.recordset)
    }

    func testJSONValueKeepsIntegersAndDisplaysNumbers() throws {
        let value = try JSONDecoder().decode(JSONValue.self, from: Data(#"{"a":5,"b":193.0,"c":1.5,"d":true,"e":null}"#.utf8))
        XCTAssertEqual(value["a"], .int(5))
        XCTAssertEqual(value["b"]?.display, "193")
        XCTAssertEqual(value["c"]?.display, "1.5")
        XCTAssertEqual(value["d"], .bool(true))
        XCTAssertEqual(value["e"], .null)
        let encoded = String(decoding: try JSONEncoder().encode(JSONValue.int(7)), as: UTF8.self)
        XCTAssertEqual(encoded, "7")
    }
}

final class ViewModelTests: XCTestCase {
    func decode<T: Decodable>(_ type: T.Type, _ json: String) throws -> T {
        try HeliadJSON.decoder().decode(T.self, from: Data(json.utf8))
    }

    /// Shaped like the core's test_coverage.py results.
    func testCoverageAndSearchBins() throws {
        let cov = try decode(Coverage.self, """
        {"start":"2025-09-21T16:00:00.000","end":"2025-09-21T17:00:00.000","bins":3,"bin_seconds":1200.0,
         "datasets":[{"dataset_key":"a","label":"SDO AIA 171 Å L1","instrument":"AIA","wavelength":171.0,
                      "counts":[0,1,0],"frames":1,"first":"2025-09-21T16:30:00.000","last":"2025-09-21T16:30:00.000","undated":0},
                     {"dataset_key":"b","label":"SDO AIA 193 Å L1","instrument":"AIA","wavelength":193.0,
                      "counts":[3,0,1],"frames":4,"first":null,"last":null,"undated":2}]}
        """)
        XCTAssertEqual(cov.total, [3, 1, 1])
        XCTAssertEqual(cov.datasets.map(\.id), ["a", "b"])
        XCTAssertEqual(cov.datasets[1].undated, 2)
        let empty = try decode(Coverage.self, #"{"start":null,"end":null,"bins":4,"bin_seconds":null,"datasets":[]}"#)
        XCTAssertNil(empty.start)
        XCTAssertEqual(empty.total, [0, 0, 0, 0])
        let fido = try decode(SearchResult.self, """
        {"total":3,"providers":[{"client":"VSOClient","count":3,"columns":[],"rows":[],
          "coverage":{"start":"2025-09-21T16:00:00.000","end":"2025-09-21T17:00:00.000","bins":2,"bin_seconds":1800.0,
                      "counts":[1,2],"outside":0,"undated":0}}]}
        """)
        XCTAssertEqual(fido.providers[0].coverage?.counts, [1, 2])
        let jsoc = try decode(SearchResult.self, """
        {"recordset":"x","total":1,"rows":[{"T_REC":"2025-09-21T16:00:00Z"}],
         "coverage":{"start":"2025-09-21T16:00:00.000","end":"2025-09-21T16:05:00.000","bins":1,"bin_seconds":300.0,
                     "counts":[1],"outside":0,"undated":0}}
        """)
        XCTAssertEqual(jsoc.providers[0].coverage?.binSeconds, 300)
    }

    func testEventAndChecks() throws {
        let events = try decode([EventView].self, """
        [{"view_id":1,"name":"flare","kind":"event","description":"M1","created_at":"2026-10-19T03:00:00+00:00",
          "observations":4,"datasets":2,"bytes":1024,"first":"2025-09-21T16:00:00.000","last":"2025-09-21T16:30:00.000",
          "path":"/lib/views/flare","materialized":false}]
        """)
        XCTAssertEqual(events[0].id, "flare")
        XCTAssertFalse(events[0].materialized)
        let checks = try decode([FileCheck].self, #"[{"code":"wcs.missing","severity":"warning","message":"no CTYPE1"}]"#)
        XCTAssertEqual(checks[0].severity, "warning")
    }
}

final class IndexTimeTests: XCTestCase {
    func testReadsAndWritesIndexText() throws {
        let t = try XCTUnwrap(IndexTime.date("2025-09-21T16:00:04.840"))
        XCTAssertEqual(IndexTime.text(t), "2025-09-21T16:00:04.840")
        XCTAssertEqual(IndexTime.date("2025-09-21T16:00:04.84Z"), t)
        XCTAssertEqual(IndexTime.date("2025-09-21 16:00:04.8401"), t)
        XCTAssertEqual(IndexTime.text(try XCTUnwrap(IndexTime.date("2025-09-21 16:00"))), "2025-09-21T16:00:00.000")
        XCTAssertEqual(IndexTime.text(try XCTUnwrap(IndexTime.date("2025-09-21"))), "2025-09-21T00:00:00.000")
        XCTAssertNil(IndexTime.date("yesterday"))
        XCTAssertNil(IndexTime.date("2025-09-21T16:00:04."))
        XCTAssertNil(IndexTime.date(nil))
        XCTAssertEqual(IndexTime.wholeSeconds("2025-09-21T16:00:04.840", up: false), "2025-09-21T16:00:04")
        XCTAssertEqual(IndexTime.wholeSeconds("2025-09-21T16:00:04.840", up: true), "2025-09-21T16:00:05")
        XCTAssertEqual(IndexTime.wholeSeconds("2025-09-21T16:00:04.000", up: true), "2025-09-21T16:00:04")
    }

    func testTicksLandOnRoundTimes() throws {
        let start = try XCTUnwrap(IndexTime.date("2025-09-21T16:00:04.840"))
        let end = try XCTUnwrap(IndexTime.date("2025-09-21T17:00:00"))
        XCTAssertEqual(IndexTime.step(from: start, to: end, count: 8), 600)
        let ticks = IndexTime.ticks(from: start, to: end, count: 8).map(IndexTime.text)
        XCTAssertEqual(ticks.first, "2025-09-21T16:10:00.000")
        XCTAssertEqual(ticks.last, "2025-09-21T17:00:00.000")
        XCTAssertEqual(ticks.count, 6)
        XCTAssertEqual(IndexTime.label(try XCTUnwrap(IndexTime.date(ticks[0])), step: 600, showDate: false), "16:10")
        let week = try XCTUnwrap(IndexTime.date("2025-09-28"))
        XCTAssertEqual(IndexTime.step(from: start, to: week, count: 8), 86400)
    }
}

final class QueryFieldsTests: XCTestCase {
    func testValidQuery() throws {
        var f = QueryFields()
        f.start = "2025-09-21 16:00"
        f.end = "2025-09-21T16:05:00Z"
        f.instrument = " AIA "
        f.wavelength = "193"
        f.sample = "60"
        let q = try f.json()
        XCTAssertEqual(q["start"], .string("2025-09-21T16:00"))
        XCTAssertEqual(q["instrument"], .string("AIA"))
        XCTAssertEqual(q["wavelength"], .double(193))
        XCTAssertEqual(q["sample_s"], .double(60))
        XCTAssertNil(q["jsoc_series"])
        XCTAssertEqual(QueryFields(query: q).wavelength, "193")
    }

    func testProblems() {
        var f = QueryFields()
        XCTAssertThrowsError(try f.json()) { XCTAssertEqual($0 as? QueryFields.Problem, .missing("Start")) }
        f.start = "yesterday"
        f.end = "2025-09-21"
        XCTAssertThrowsError(try f.json()) { XCTAssertEqual($0 as? QueryFields.Problem, .badTime("Start")) }
        f.start = "2025-09-21T17:00"
        f.end = "2025-09-21T16:00:00"
        XCTAssertThrowsError(try f.json()) { XCTAssertEqual($0 as? QueryFields.Problem, .endBeforeStart) }
        f.end = "2025-09-22"
        f.wavelength = "one nine three"
        XCTAssertThrowsError(try f.json()) { XCTAssertEqual($0 as? QueryFields.Problem, .badNumber("Wavelength")) }
    }
}

final class CoreLocatorTests: XCTestCase {
    let home = URL(fileURLWithPath: "/Users/someone")

    func testOrderAndArguments() throws {
        let everything: (String) -> Bool = { _ in true }
        var launch = try CoreLocator.launch(library: "~/Lib", corePath: nil, bundle: Bundle(for: Self.self),
                                            environment: ["HELIAD_CORE": "/opt/py/bin/python3"],
                                            home: home, exists: everything)
        XCTAssertEqual(launch.executable.path, "/opt/py/bin/python3")
        XCTAssertEqual(launch.arguments, ["-m", "heliad", "--library", "/Users/someone/Lib", "serve"])
        launch = try CoreLocator.launch(library: nil, corePath: nil, bundle: Bundle(for: Self.self),
                                        environment: [:], home: home,
                                        exists: { $0.hasSuffix(CoreLocator.devCheckout) })
        XCTAssertEqual(launch.executable.path, "/Users/someone/" + CoreLocator.devCheckout)
        XCTAssertEqual(launch.arguments, ["serve"])
    }

    func testNothingFound() {
        XCTAssertThrowsError(try CoreLocator.launch(library: nil, corePath: "/nope", bundle: Bundle(for: Self.self),
                                                    environment: [:], home: home, exists: { _ in false }))
    }
}

/// The client against a stand-in core written in sh: it answers every request with its
/// id, sends one notification, and answers "slow" after "fast" to show out-of-order replies.
final class CoreClientTests: XCTestCase {
    static let script = #"""
    printf '{"jsonrpc":"2.0","method":"download.progress","params":{"job_id":1}}\n'
    slow=""
    while IFS= read -r line; do
      id=$(printf '%s' "$line" | sed -E 's/.*"id":([0-9]+).*/\1/')
      case "$line" in
        *'"method":"slow"'*) slow=$id ;;
        *'"method":"boom"'*) printf '{"jsonrpc":"2.0","id":%s,"error":{"code":-32601,"message":"no method boom"}}\n' "$id" ;;
        *) printf '{"jsonrpc":"2.0","id":%s,"result":{"id":%s,"ok":true}}\n' "$id" "$id"
           if [ -n "$slow" ]; then printf '{"jsonrpc":"2.0","id":%s,"result":{"id":%s,"ok":true}}\n' "$slow" "$slow"; slow=""; fi ;;
      esac
    done
    """#

    struct Echo: Decodable { let id: Int; let ok: Bool }

    final class Notes: @unchecked Sendable {
        var methods: [String] = []
        let lock = NSLock()
        func add(_ m: String) { lock.lock(); methods.append(m); lock.unlock() }
    }

    func testRequestsRepliesAndNotifications() async throws {
        let client = CoreClient()
        let notes = Notes()
        client.onNotification = { method, _ in notes.add(method) }
        try client.start(.init(executable: URL(fileURLWithPath: "/bin/sh"), arguments: ["-c", Self.script]))
        async let slow = client.call("slow", as: Echo.self)
        try await Task.sleep(nanoseconds: 200_000_000)
        let fast = try await client.call("fast", ["n": .int(1)], as: Echo.self)
        let late = try await slow
        XCTAssertTrue(fast.ok && late.ok)
        XCTAssertNotEqual(fast.id, late.id)
        do {
            _ = try await client.callRaw("boom")
            XCTFail("expected an error")
        } catch let error as RPCError {
            XCTAssertEqual(error.code, -32601)
        }
        XCTAssertEqual(notes.methods, ["download.progress"])
        await client.stop(grace: 2)
        XCTAssertFalse(client.isRunning)
        do {
            _ = try await client.callRaw("fast")
            XCTFail("expected not running")
        } catch is CoreError {}
    }

    func testCoreThatDiesIsReported() async throws {
        let client = CoreClient()
        let exited = expectation(description: "exit reported")
        client.onExit = { status, stderr in
            XCTAssertEqual(status, 3)
            XCTAssertTrue(stderr.contains("LibraryLocked"))
            exited.fulfill()
        }
        try client.start(.init(executable: URL(fileURLWithPath: "/bin/sh"),
                               arguments: ["-c", "echo 'heliad.library.LibraryLocked: another Heliad process' >&2; exit 3"]))
        await fulfillment(of: [exited], timeout: 5)
    }

    /// The real core, when HELIAD_CORE names it (the build script sets it).
    func testRealCoreHello() async throws {
        guard let core = ProcessInfo.processInfo.environment["HELIAD_CORE"], !core.isEmpty,
              let library = ProcessInfo.processInfo.environment["HELIAD_TEST_LIBRARY"] else {
            throw XCTSkip("set HELIAD_CORE and HELIAD_TEST_LIBRARY to run against the real core")
        }
        let launch = try CoreLocator.launch(library: library, corePath: nil, bundle: Bundle(for: Self.self))
        XCTAssertEqual(launch.executable.path, core)
        let client = CoreClient()
        try client.start(launch)
        let hello = try await client.call("heliad.hello", as: Hello.self)
        XCTAssertEqual(hello.readerCompat, 1)
        XCTAssertTrue(hello.methods.contains("library.thumbnail"))
        _ = try await client.call("library.stats", as: Stats.self)
        _ = try await client.call("library.datasets", as: [Dataset].self)
        _ = try await client.call("download.list", as: [Job].self)
        await client.stop()
    }
}
