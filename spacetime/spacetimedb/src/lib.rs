// Multi-Agent CFO OS — SpacetimeDB Module
// Real-time backend for Collaborative Hardening Protocol (CHP) orchestration.
//
// Tables
// ======
// 1. Brief                — Input briefs that kick off a new analysis
// 2. AgentTurnRecord      — Per-agent reasoning-cycle records
// 3. SharedContextEntity  — Entity/Event/Task shared across agents
// 4. DecisionCase         — CHP-hardened decision state machine
// 5. AuditEntry           — Per-claim audit trail entries
// 6. FinalArtifact        — Rendered output artifacts
//
// Reducers
// ========
// submit_brief            — Create a new CFO analysis brief
// record_agent_turn       — Record an agent reasoning cycle
// update_decision_state   — Advance a decision through CHP phases
// publish_artifact        — Publish a final rendered artifact
// seed_briefs             — Bulk-insert sample briefs for testing

use spacetimedb::{ReducerContext, Table};

// ---------------------------------------------------------------------------
// 1. Brief
// ---------------------------------------------------------------------------

#[spacetimedb::table(accessor = brief, public)]
pub struct Brief {
    #[primary_key]
    brief_id: String,
    title: String,
    company: String,
    problem: String,
    task_type: String, // "Forecast" | "InvestmentCase" | "BoardOutput"
    priority: u32,
    constraints: String,
    created_at: u64, // unix ms timestamp
}

// ---------------------------------------------------------------------------
// 2. AgentTurnRecord
// ---------------------------------------------------------------------------

#[spacetimedb::table(accessor = agent_turn_record, public)]
pub struct AgentTurnRecord {
    #[primary_key]
    turn_id: String,
    brief_id: String,
    agent_name: String, // "Finance" | "Strategy" | "Compliance"
    status: String,     // "InProgress" | "Complete" | "Blocked"
    expansion_text: String,
    compression_text: String,
    confidence: f64,
    created_at: u64,
}

// ---------------------------------------------------------------------------
// 3. SharedContextEntity
// ---------------------------------------------------------------------------

#[spacetimedb::table(accessor = shared_context_entity, public)]
pub struct SharedContextEntity {
    #[primary_key]
    entity_id: String,
    brief_id: String,
    entity_type: String, // "Entity" | "Event" | "Task"
    name: String,
    properties: String,     // JSON string
    semantic_tags: String,  // JSON-encoded Vec<String>: "[\"tag1\",\"tag2\"]"
}

// ---------------------------------------------------------------------------
// 4. DecisionCase
// ---------------------------------------------------------------------------

#[spacetimedb::table(accessor = decision_case, public)]
pub struct DecisionCase {
    #[primary_key]
    decision_id: String,
    brief_id: String,
    phase: u8, // 0 | 1 | 2 | 3
    round_num: u32,
    status: String, // "Exploring" | "ProvisionalLock" | "Locked"
    foundation_score: Option<u32>,
    adversary_score: Option<u32>,
}

// ---------------------------------------------------------------------------
// 5. AuditEntry
// ---------------------------------------------------------------------------

#[spacetimedb::table(accessor = audit_entry, public)]
pub struct AuditEntry {
    #[primary_key]
    audit_id: String,
    decision_id: String,
    producer: String, // agent name or system
    claim: String,
    grounding: String,
    confidence: f64,
    chp_finding: String, // CHP finding label
    created_at: u64,
}

// ---------------------------------------------------------------------------
// 6. FinalArtifact
// ---------------------------------------------------------------------------

#[spacetimedb::table(accessor = final_artifact, public)]
pub struct FinalArtifact {
    #[primary_key]
    artifact_id: String,
    brief_id: String,
    task_type: String,
    rendered_markdown: String,
    total_time_ms: u64,
    version: u32,
}

// ===========================================================================
// INIT / CONNECT / DISCONNECT
// ===========================================================================

#[spacetimedb::reducer(init)]
pub fn init(_ctx: &ReducerContext) {
    log::info!("CFO OS SpacetimeDB module initialized");
}

#[spacetimedb::reducer(client_connected)]
pub fn identity_connected(_ctx: &ReducerContext) {
    log::info!("Client connected");
}

#[spacetimedb::reducer(client_disconnected)]
pub fn identity_disconnected(_ctx: &ReducerContext) {
    log::info!("Client disconnected");
}

// ===========================================================================
// REDUCERS
// ===========================================================================

/// submit_brief — Kicks off a new CFO analysis
#[spacetimedb::reducer]
pub fn submit_brief(
    ctx: &ReducerContext,
    brief_id: String,
    title: String,
    company: String,
    problem: String,
    task_type: String,
    priority: u32,
    constraints: String,
) {
    let now_ms = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis() as u64;

    let id = brief_id.clone();
    ctx.db.brief().insert(Brief {
        brief_id: id.clone(),
        title,
        company,
        problem,
        task_type,
        priority,
        constraints,
        created_at: now_ms,
    });

    log::info!("Brief submitted: {}", id);
}

/// record_agent_turn — Agent reports a reasoning cycle
#[spacetimedb::reducer]
pub fn record_agent_turn(
    ctx: &ReducerContext,
    turn_id: String,
    brief_id: String,
    agent_name: String,
    status: String,
    expansion_text: String,
    compression_text: String,
    confidence: f64,
) {
    let now_ms = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis() as u64;

    let bs = brief_id.clone();
    ctx.db.agent_turn_record().insert(AgentTurnRecord {
        turn_id,
        brief_id: bs.clone(),
        agent_name,
        status,
        expansion_text,
        compression_text,
        confidence: confidence.clamp(0.0, 1.0),
        created_at: now_ms,
    });

    log::info!("Agent turn recorded for brief: {}", bs);
}

/// update_decision_state — CHP state machine
/// Finds the existing DecisionCase by PK, modifies fields, and updates.
#[spacetimedb::reducer]
pub fn update_decision_state(
    ctx: &ReducerContext,
    decision_id: String,
    new_status: String,
    foundation_score: Option<u32>,
    adversary_score: Option<u32>,
) {
    // Validate status
    match new_status.as_str() {
        "Exploring" | "ProvisionalLock" | "Locked" => {}
        other => {
            log::warn!("Invalid decision status '{}' — ignoring", other);
            return;
        }
    }

    let score = foundation_score.map(|s| std::cmp::min(s, 100));
    let adv = adversary_score.map(|s| std::cmp::min(s, 100));

    // Find existing row by PK, build updated row, then update-in-place
    if let Some(mut existing) = ctx.db.decision_case().decision_id().find(&decision_id) {
        existing.status = new_status;
        if let Some(s) = score {
            existing.foundation_score = Some(s);
        }
        if let Some(s) = adv {
            existing.adversary_score = Some(s);
        }
        ctx.db.decision_case().decision_id().update(existing);
        log::info!("Decision {} status updated", decision_id);
    } else {
        log::warn!("DecisionCase {} not found — cannot update", decision_id);
    }
}

/// publish_artifact — Final artifact published
#[spacetimedb::reducer]
pub fn publish_artifact(
    ctx: &ReducerContext,
    artifact_id: String,
    brief_id: String,
    task_type: String,
    rendered_markdown: String,
    total_time_ms: u64,
    version: u32,
) {
    let bs = brief_id.clone();
    ctx.db.final_artifact().insert(FinalArtifact {
        artifact_id,
        brief_id: bs.clone(),
        task_type,
        rendered_markdown,
        total_time_ms,
        version,
    });

    log::info!("Artifact published for brief: {}", bs);
}

// ===========================================================================
// HELPER REDUCER: seed data (used in testing or initial setup)
// ===========================================================================

/// seed_briefs — Bulk-insert sample brief rows for dev/testing
#[spacetimedb::reducer]
pub fn seed_briefs(ctx: &ReducerContext) {
    let briefs: [(&str, &str, &str, &str, &str, u32, &str); 3] = [
        (
            "b-001",
            "FY2027 Revenue Forecast",
            "AcmeCorp",
            "Forecast FY2027 revenue under base, upside, and stress scenarios",
            "Forecast",
            1,
            "Use GAAP standards",
        ),
        (
            "b-002",
            "M&A Investment Case",
            "BetaTech",
            "Evaluate acquisition target at $45M EV with 3x revenue multiple",
            "InvestmentCase",
            2,
            "Due diligence complete",
        ),
        (
            "b-003",
            "Q3 Board Deck",
            "GammaInc",
            "Quarterly board deck with strategic update, financials, and risk outlook",
            "BoardOutput",
            1,
            "Board meeting May 15",
        ),
    ];

    let now_ms = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis() as u64;

    for (id, title, company, problem, ttype, prio, constraints) in briefs {
        ctx.db.brief().insert(Brief {
            brief_id: id.to_string(),
            title: title.to_string(),
            company: company.to_string(),
            problem: problem.to_string(),
            task_type: ttype.to_string(),
            priority: prio,
            constraints: constraints.to_string(),
            created_at: now_ms,
        });
    }

    log::info!("Seeded {} briefs", briefs.len());
}
