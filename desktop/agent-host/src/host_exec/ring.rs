//! A process's output, kept in a bounded ring with 1-based sequences.
//!
//! The same semantics as `sandbox_runtime`'s `OutputBuffer`, because the
//! provider reads both through one cursor: a chunk's sequence is fixed when it
//! arrives, `after_sequence` is exclusive, and when old output is dropped to
//! stay under the limit `truncated_before_sequence` says where what is left
//! begins.

use std::collections::VecDeque;

use serde::Serialize;

/// Which stream a chunk came from. A PTY merges stdout and stderr.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum Stream {
    Stdout,
    Stderr,
    Pty,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Chunk {
    pub sequence: u64,
    pub stream: Stream,
    pub data: Vec<u8>,
}

#[derive(Debug)]
pub struct OutputRing {
    limit: usize,
    chunks: VecDeque<Chunk>,
    bytes: usize,
    next_sequence: u64,
    truncated_before_sequence: Option<u64>,
}

/// What a read sees.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Snapshot {
    pub chunks: Vec<Chunk>,
    pub next_sequence: u64,
    pub truncated_before_sequence: Option<u64>,
}

impl OutputRing {
    #[must_use]
    pub fn new(limit: usize) -> Self {
        Self {
            limit: limit.max(1),
            chunks: VecDeque::new(),
            bytes: 0,
            next_sequence: 1,
            truncated_before_sequence: None,
        }
    }

    pub fn push(&mut self, stream: Stream, mut data: Vec<u8>) {
        if data.is_empty() {
            return;
        }
        // One chunk larger than the whole ring keeps its tail: the end of a
        // burst is what someone reading a command's output wants.
        if data.len() > self.limit {
            data.drain(..data.len() - self.limit);
        }
        self.bytes += data.len();
        self.chunks.push_back(Chunk {
            sequence: self.next_sequence,
            stream,
            data,
        });
        self.next_sequence += 1;
        while self.bytes > self.limit {
            let Some(removed) = self.chunks.pop_front() else {
                break;
            };
            self.bytes -= removed.data.len();
            self.truncated_before_sequence = Some(removed.sequence + 1);
        }
    }

    #[must_use]
    pub fn next_sequence(&self) -> u64 {
        self.next_sequence
    }

    /// What follows `after_sequence`, up to `max_bytes` of data -- always at
    /// least one chunk when there is one. When it stops short,
    /// `next_sequence` is the first chunk it left out, so a reader continuing
    /// from the last chunk it got carries on where it should.
    #[must_use]
    pub fn read_at_most(&self, after_sequence: u64, max_bytes: usize) -> Snapshot {
        let mut snapshot = self.read(after_sequence);
        let mut total = 0_usize;
        let keep = snapshot
            .chunks
            .iter()
            .take_while(|chunk| {
                let first = total == 0;
                total += chunk.data.len();
                first || total <= max_bytes
            })
            .count();
        if keep < snapshot.chunks.len() {
            snapshot.next_sequence = snapshot.chunks[keep].sequence;
            snapshot.chunks.truncate(keep);
        }
        snapshot
    }

    /// Everything after `after_sequence`, which is exclusive.
    #[must_use]
    pub fn read(&self, after_sequence: u64) -> Snapshot {
        Snapshot {
            chunks: self
                .chunks
                .iter()
                .filter(|chunk| chunk.sequence > after_sequence)
                .cloned()
                .collect(),
            next_sequence: self.next_sequence,
            truncated_before_sequence: self.truncated_before_sequence,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn sequences_start_at_one_and_after_sequence_is_exclusive() {
        let mut ring = OutputRing::new(1024);
        ring.push(Stream::Stdout, b"a".to_vec());
        ring.push(Stream::Stderr, b"b".to_vec());
        ring.push(Stream::Stdout, Vec::new());
        let all = ring.read(0);
        assert_eq!(
            all.chunks
                .iter()
                .map(|chunk| chunk.sequence)
                .collect::<Vec<_>>(),
            [1, 2]
        );
        assert_eq!(all.next_sequence, 3);
        assert_eq!(all.truncated_before_sequence, None);
        let rest = ring.read(1);
        assert_eq!(rest.chunks.len(), 1);
        assert_eq!(rest.chunks[0].data, b"b");
        assert!(ring.read(2).chunks.is_empty());
    }

    #[test]
    fn old_output_is_dropped_to_stay_under_the_limit_and_says_so() {
        let mut ring = OutputRing::new(4);
        ring.push(Stream::Stdout, b"12".to_vec());
        ring.push(Stream::Stdout, b"34".to_vec());
        ring.push(Stream::Stdout, b"56".to_vec());
        let snapshot = ring.read(0);
        assert_eq!(snapshot.truncated_before_sequence, Some(2));
        assert_eq!(
            snapshot
                .chunks
                .iter()
                .map(|chunk| chunk.data.clone())
                .collect::<Vec<_>>(),
            [b"34".to_vec(), b"56".to_vec()]
        );
        assert_eq!(snapshot.next_sequence, 4);
    }

    #[test]
    fn a_read_stops_at_its_byte_budget_and_says_where_to_go_on() {
        let mut ring = OutputRing::new(1024);
        for data in [b"aaaa", b"bbbb", b"cccc"] {
            ring.push(Stream::Stdout, data.to_vec());
        }
        let first = ring.read_at_most(0, 6);
        assert_eq!(first.chunks.len(), 1);
        assert_eq!(first.next_sequence, 2);
        let rest = ring.read_at_most(1, 8);
        assert_eq!(rest.chunks.len(), 2);
        assert_eq!(rest.next_sequence, 4);
        // A chunk bigger than the budget still goes, alone.
        assert_eq!(ring.read_at_most(0, 1).chunks.len(), 1);
    }

    #[test]
    fn a_chunk_larger_than_the_ring_keeps_its_tail() {
        let mut ring = OutputRing::new(3);
        ring.push(Stream::Pty, b"abcdef".to_vec());
        let snapshot = ring.read(0);
        assert_eq!(snapshot.chunks[0].data, b"def");
        assert_eq!(snapshot.truncated_before_sequence, None);
    }
}
