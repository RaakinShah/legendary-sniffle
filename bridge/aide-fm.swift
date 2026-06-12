// aide-fm — a thin Swift bridge that lets Aide's Python backend drive Apple's
// Foundation Models (on-device today, Private Cloud Compute once the macOS 27 SDK
// is installed). The Python SDK (apple-fm-sdk) only exposes the on-device model,
// so reaching the larger PCC model requires the native framework — this binary.
//
// Protocol: line-delimited JSON, both directions.
//   Python -> stdin:
//     {"op":"init","system":"...","cloud":false,"tools":[{"name","description","parameters":<jsonschema>}]}
//     {"op":"turn","message":"..."}
//     {"op":"tool_result","id":N,"result":"..."}
//     {"op":"shutdown"}
//   Swift -> stdout (one JSON object per line):
//     {"type":"ready"}                                  after init
//     {"type":"delta","text":"..."}                     streamed text
//     {"type":"tool_call","id":N,"name":"...","args":"<json>"}   model invoked a tool
//     {"type":"final","text":"..."}                     full turn text
//     {"type":"done"}                                   turn finished
//     {"type":"error","message":"..."}
//
// A tool's call() writes a tool_call and then blocks on stdin for the matching
// tool_result, so Aide's real Python tools (tasks, memory, recall, …) execute the
// work and hand the model back a string. Generation pauses there exactly as it
// would for an in-process tool.

import Foundation
import FoundationModels

// MARK: - JSON line I/O

let stdoutLock = NSLock()

func emit(_ obj: [String: Any]) {
    guard let data = try? JSONSerialization.data(withJSONObject: obj),
          let line = String(data: data, encoding: .utf8) else { return }
    stdoutLock.lock()
    print(line)
    fflush(stdout)
    stdoutLock.unlock()
}

func readLineJSON() -> [String: Any]? {
    guard let line = readLine(strippingNewline: true),
          let data = line.data(using: .utf8),
          let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
    else { return nil }
    return obj
}

// MARK: - Dynamic schema from a JSON-schema dict

func buildSchema(name: String, _ params: [String: Any]) throws -> GenerationSchema {
    let properties = (params["properties"] as? [String: [String: Any]]) ?? [:]
    let required = Set((params["required"] as? [String]) ?? [])
    var props: [DynamicGenerationSchema.Property] = []
    for (pname, pdef) in properties {
        let desc = pdef["description"] as? String
        let propSchema: DynamicGenerationSchema
        if let choices = pdef["enum"] as? [String] {
            propSchema = DynamicGenerationSchema(name: pname, description: desc, anyOf: choices)
        } else {
            switch pdef["type"] as? String {
            case "integer": propSchema = DynamicGenerationSchema(type: Int.self)
            case "number":  propSchema = DynamicGenerationSchema(type: Double.self)
            case "boolean": propSchema = DynamicGenerationSchema(type: Bool.self)
            default:        propSchema = DynamicGenerationSchema(type: String.self)
            }
        }
        props.append(.init(name: pname, description: desc, schema: propSchema,
                           isOptional: !required.contains(pname)))
    }
    let root = DynamicGenerationSchema(name: name, properties: props)
    return try GenerationSchema(root: root, dependencies: [])
}

// MARK: - Bridge tool

// A unique id per tool invocation, so a tool_result can be matched to its call.
final class CallCounter: @unchecked Sendable {
    private let lock = NSLock()
    private var n = 0
    func next() -> Int { lock.lock(); defer { lock.unlock() }; n += 1; return n }
}
let callCounter = CallCounter()

struct BridgeTool: Tool {
    typealias Arguments = GeneratedContent
    typealias Output = String

    let name: String
    let description: String
    let parameters: GenerationSchema

    func call(arguments: GeneratedContent) async throws -> String {
        let id = callCounter.next()
        let argsJSON = arguments.jsonString
        emit(["type": "tool_call", "id": id, "name": name, "args": argsJSON])
        // Block until Python returns the matching result.
        while let msg = readLineJSON() {
            if (msg["op"] as? String) == "tool_result",
               (msg["id"] as? Int) == id {
                return (msg["result"] as? String) ?? ""
            }
            // Anything else mid-tool is unexpected; surface it rather than hang.
            if (msg["op"] as? String) == "shutdown" { exit(0) }
        }
        return "(no result returned by the host)"
    }
}

// MARK: - Model selection

func makeModel(cloud: Bool) -> SystemLanguageModel {
    // PCC INSERTION POINT: once compiled against the macOS 27 SDK, replace this
    // with `if cloud, #available(...) { return ... PrivateCloudComputeLanguageModel-backed model ... }`.
    // The 26.x SDK only exposes the on-device model, so `cloud` is a no-op today.
    if cloud {
        FileHandle.standardError.write("aide-fm: cloud requested but this SDK has only the on-device model\n".data(using: .utf8)!)
    }
    return SystemLanguageModel.default
}

// MARK: - Main loop

@main
struct AideFM {
    static func main() async {
        guard let initMsg = readLineJSON(), (initMsg["op"] as? String) == "init" else {
            emit(["type": "error", "message": "expected init"]); return
        }
        let system = initMsg["system"] as? String ?? ""
        let cloud = initMsg["cloud"] as? Bool ?? false
        let model = makeModel(cloud: cloud)

        switch model.availability {
        case .available: break
        case .unavailable(let reason):
            emit(["type": "error", "message": "model unavailable: \(reason)"]); return
        }

        var tools: [any Tool] = []
        for spec in (initMsg["tools"] as? [[String: Any]]) ?? [] {
            guard let name = spec["name"] as? String,
                  let desc = spec["description"] as? String,
                  let params = spec["parameters"] as? [String: Any] else { continue }
            do {
                let schema = try buildSchema(name: name, params)
                tools.append(BridgeTool(name: name, description: desc, parameters: schema))
            } catch {
                FileHandle.standardError.write("aide-fm: bad schema for \(name): \(error)\n".data(using: .utf8)!)
            }
        }

        let session = LanguageModelSession(model: model, tools: tools, instructions: system)
        emit(["type": "ready"])

        while let msg = readLineJSON() {
            switch msg["op"] as? String {
            case "turn":
                let message = msg["message"] as? String ?? ""
                await runTurn(session: session, message: message)
            case "shutdown":
                return
            default:
                emit(["type": "error", "message": "unknown op"])
            }
        }
    }

    static func runTurn(session: LanguageModelSession, message: String) async {
        do {
            var prev = ""
            let stream = session.streamResponse(to: message)
            for try await partial in stream {
                let text = partial.content
                if text.count > prev.count, text.hasPrefix(prev) {
                    emit(["type": "delta", "text": String(text.dropFirst(prev.count))])
                } else if text != prev {
                    emit(["type": "delta", "text": text])
                }
                prev = text
            }
            emit(["type": "final", "text": prev])
            emit(["type": "done"])
        } catch {
            emit(["type": "error", "message": "\(error)"])
            emit(["type": "done"])
        }
    }
}
