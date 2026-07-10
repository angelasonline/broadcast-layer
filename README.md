# Broadcast Layer

**Access as broadcast infrastructure.**

Closed captioning is treated as essential infrastructure in broadcast, with standardized formats, vendor ecosystems, and regulatory oversight. Sign language is not. It remains episodic, discretionary, and inconsistently delivered, even though it is the primary language for many Deaf viewers and captions are not linguistically equivalent.

Broadcast Layer is the delivery layer that treats real-time access as infrastructure. Human captioners and interpreters feed standards-compliant access tracks, with redundant sources, honest failover, and a signed compliance record for every cue. Captions and sign language are peers, not afterthoughts.

## Why Broadcast Layer?

* Access tracks are delivered like the signal itself: reliable, standardized, and monitored.
* Captions and sign language are first-class and equal.
* The human is the standard. Automation is a labeled backstop, never a replacement.
* Every cue is signed, so compliance is a byproduct, not a separate task.
* It runs where the broadcaster needs it: cloud, VPC, or on-prem.

## What it does

Broadcast Layer sits between the people who produce access (captioners, interpreters) and the players and pipelines that carry a broadcast. It manages tracks, source redundancy, and standards-compliant output, and it records a signed receipt for every cue.

```
        Captioners & Interpreters
                    |
                    v
          +-------------------+
          |  Broadcast Layer  |
          +-------------------+
          | Tracks            |
          | Source redundancy |
          | Handoff           |
          | Signed receipts   |
          | WebVTT / TTML      |
          | Compliance export |
          +-------------------+
              |      |      |
              v      v      v
           Players Pipelines Regulators
```

## Enterprise capabilities

* Caption and sign-language tracks as co-equal peers
* Redundant sources per track: primary human, hot standby, AI backstop
* Seamless handoff with no gap when a source drops
* Honest labeling of every cue's source (human or AI, primary or backstop)
* Standards-compliant output: WebVTT and TTML, plus a live WebSocket feed
* Correction buffer and broadcast-delay offset in the captioner console
* HMAC-signed cue receipts
* Exportable per-broadcast compliance report (coverage, sources, handoffs)
* Live operations dashboard: active source, cue rate, source health, handoffs
* Deployment in cloud, customer VPC, or on-prem
* Security posture: content not persisted beyond the broadcast, TLS in transit, no viewer PII, SSO-ready
* Tests and continuous integration

## Consoles

* `/caption` captioner and interpreter console (live input, correction buffer, stream-delay offset, primary or hot-standby role)
* `/dashboard` operations view (live tracks, active source, cue rate, health)
* `/player` sample-player demo showing captions riding a stream
* `/` overview

## Standards and compliance

Tracks export as WebVTT (`/v1/tracks/{id}.vtt`) and TTML (`/v1/tracks/{id}.ttml`), the timed-text formats broadcast and streaming pipelines ingest. Every cue is HMAC-signed; a per-broadcast compliance report is available at `/v1/broadcasts/{id}/report`, giving coverage, source breakdown, handoff count, and signature verification for the whole broadcast.

## Architecture

Broadcast Layer is organized around modular components rather than platform-specific implementations.

* Track engine with source redundancy and handoff
* Signed cue model
* Standards-compliant exporters (WebVTT, TTML)
* Live cue transport over WebSocket
* Compliance reporting
* Operations dashboard

Sign-language tracks carry interpreter video segment references rather than timed text; the delivery, redundancy, receipt, and reporting machinery is identical to captions, which is what makes the two tracks true peers.

## Reliability

Broadcast Layer is built for the failure modes of a live broadcast, and each is covered by tests.

* **Restart recovery.** The compliance record is journaled to durable append-only storage and replayed on boot. A mid-broadcast restart loses no signed cue, and sequence numbers continue rather than reset. A torn final write from a hard crash is skipped safely.
* **Reconnect resume.** A captioner whose socket drops reconnects to the same source and sequence with a resume token, rather than orphaning the source or spawning a duplicate. The hot standby covers the gap in between.
* **Backpressure isolation.** Each subscriber has a bounded outbound queue. A slow player degrades only itself, dropping its own oldest frames and recording the drop, and never stalls the captioner or the fan-out.
* **Ordered catch-up.** Cues carry a monotonic sequence. A reconnecting player passes its last seen sequence and receives the missed cues in order.
* **Rate limiting.** Control-plane writes are token-bucket limited per client.
* **Observability.** Structured JSON logs, a health endpoint with uptime, and a readiness probe for orchestration.
* **Load.** A fan-out of 500 subscribers across a broadcast of cues sustains millions of deliveries per second with bounded per-subscriber memory. See `loadtest.py`.

These make Broadcast Layer production-grade engineering. Field-proven reliability is earned by running real broadcasts; the architecture is built so that hardening comes from operation, not rewrites.

## Standards and evidence

Broadcast Layer implements the delivery model that international broadcast standards describe, grounded in Invest In Access's practice-based work: seven years of language-access delivery across 189 live events with 39 partner organizations, including 324 ASL interpreter engagements and 31 CART captioners across 11 states and the District of Columbia.

### Captions: FCC 47 C.F.R. 79.1

The FCC defines four caption quality standards, and the per-broadcast compliance report speaks to each by name.

* **Accuracy.** Captions are produced by a professional captioner. AI backstop cues are labeled and never counted as human accuracy; the correction buffer supports fixing a line before air. Final accuracy is confirmed by human review, as the rule contemplates.
* **Synchronicity.** Cues carry media timestamps aligned to a per-broadcast delay offset, so caption timing matches air, addressing the rule's live-programming synchronicity factor.
* **Completeness.** Cues run for the broadcast; coverage gaps over three seconds are flagged, addressing the rule's completeness factor.
* **Placement.** Caption placement follows the player safe-area for timed text so captions do not block essential content; sign placement follows ITU-R BT.2448.

Captions export as WebVTT and TTML, the timed-text formats broadcast and streaming pipelines ingest.

### Sign language: ITU-R BT.2448 and EBU TR 065 (HbbTV)

ITU-R BT.2448 distinguishes **open signing**, where the signer is burned into the video at the studio, from **closed signing**, where the signer is a separate, toggleable stream the viewer can switch on or off. Broadcast Layer implements closed signing, the direction broadcasters worldwide are moving. Sign tracks carry presentation parameters from the standard: separate-stream delivery, optional alpha-channel contour blending, a default lower-right placement, and user-adjustable size and position. For live broadcasts, the standard notes signer delay can be compensated by delaying the main signal, which the per-broadcast delay offset supports.

EBU TR 065 documents the practical HbbTV delivery reality: typical HbbTV devices cannot overlay a separate signer video, so closed signing is delivered as a combined video (programme plus signer) composited at the back end and launched via an HbbTV application, or presented on a second screen. Broadcast Layer records the chosen delivery mode on the sign track. In Europe, captions distribute as EBU-TT-D, the TTML profile referenced by HbbTV 2.0.

### Deaf-led evaluation and human primacy

No AI system today produces reliable sign language video, so ASL is delivered by human interpreters, with automated assistance clearly labeled and never presented as equivalent. Presentation conformance and quality are validated with Deaf subject-matter experts. This is consistent with the World Federation of the Deaf's position that captions alone are insufficient and that real-time sign language access is essential, especially in emergencies, and with U.S. Department of Justice ADA effective-communication guidance.

### References

1. Federal Communications Commission (2023). Closed captioning of video programming, 47 C.F.R. 79.1.
2. National Institute on Deafness and Other Communication Disorders (NIDCD). What Is American Sign Language (ASL)? National Institutes of Health.
3. National Association of the Deaf. Closed captioning and sign language access.
4. Humphries et al. (2019); Hall et al. (2017, 2018); Gallaudet University VL2 Research Center; Marschark et al. (2022); National Association of the Deaf (2020, 2021); U.S. Department of Justice ADA Effective Communication Guidance (2023).
5. International Telecommunication Union (2019). Technical Realisation of Signing in Digital Television, Report ITU-R BT.2448-0. Geneva: ITU.
6. European Broadcasting Union (2021). Guidelines for Delivering Accessibility Services Using HbbTV, EBU TR 065.

## Service levels and deployment

Broadcast Layer is designed for low-latency live operation and continuous availability during a broadcast. Formal latency and uptime SLAs, SSO integration, and on-prem hardening are set per deployment contract and environment. See `deploy/DEPLOY.md`.

## Design philosophy

Access to language is a human right. In broadcast, that means real-time captions and sign language delivered with the same reliability, standardization, and accountability as the signal itself.
