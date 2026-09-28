// Local text recognition for screenshots (Apple Vision). No network, nothing leaves the Mac.
// Build once:  swiftc -O -o ~/PhoneFarm/ocr ocr.swift
// Usage:       ocr <image.png>   -> JSON [{"text":..., "x":..., "y":..., "w":..., "h":..., "conf":...}, ...]
// Coordinates are in image pixels with the origin at the top-left.
import Foundation
import Vision
import AppKit

let args = CommandLine.arguments
guard args.count >= 2, let img = NSImage(contentsOfFile: args[1]),
      let cg = img.cgImage(forProposedRect: nil, context: nil, hints: nil) else {
    FileHandle.standardError.write("usage: ocr <image>\n".data(using: .utf8)!)
    exit(2)
}
let width = CGFloat(cg.width), height = CGFloat(cg.height)
var results: [[String: Any]] = []
let request = VNRecognizeTextRequest { req, _ in
    for obs in (req.results as? [VNRecognizedTextObservation]) ?? [] {
        guard let cand = obs.topCandidates(1).first else { continue }
        let b = obs.boundingBox  // normalized, origin bottom-left
        results.append([
            "text": cand.string,
            "x": Int(b.minX * width), "y": Int((1 - b.maxY) * height),
            "w": Int(b.width * width), "h": Int(b.height * height),
            "conf": Double(cand.confidence),
        ])
    }
}
request.recognitionLevel = .accurate
request.usesLanguageCorrection = false
let handler = VNImageRequestHandler(cgImage: cg, options: [:])
try? handler.perform([request])
let data = try! JSONSerialization.data(withJSONObject: results, options: [])
FileHandle.standardOutput.write(data)
