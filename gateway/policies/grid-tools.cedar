// ---------------------------------------------------------------------------
// Minnal grid-tools Cedar policy.
// R12.1 every statement cites its requirement. R12.4 default deny.
// R12.8 this file is defence in depth only: the tools re-check everything.
// ---------------------------------------------------------------------------

// R12.2, R12.3 [SAFETY] dispatch_crew needs a well-formed clearance and a flood check
// that does not admit a hit. Every read is guarded by `has`, because the Gateway schema
// carries no requiredness Cedar can rely on and an unguarded read of an absent
// attribute is an evaluation error rather than a clean deny. Forbid wins over
// every permit below. The guard is written as `!(present && well-formed && clean)`
// rather than `!present || malformed || hit` so Cedar's strict validator can prove
// each optional read is safe (a `has` guard must precede the read in a conjunction);
// the two forms are De Morgan duals and deny on exactly the same inputs.
forbid (
  principal,
  action == AgentCore::Action::"dispatch-crew-target___dispatch_crew",
  resource
)
when {
  !(
    context.input has safety_clearance_id &&
    context.input.safety_clearance_id like "sfc_*" &&
    context.input has flood_check &&
    context.input.flood_check has intersects &&
    context.input.flood_check.intersects == false
  )
};

// R12.2, R12.3, R10.7, R10.8 [SAFETY] energise needs a clearance and a clean flood
// check; a missing field is a deny for energise. de_energise never reaches any of
// these conditions: the whole forbid is scoped to action == "energise", so a
// de_energise call with no clearance and no flood_check is simply allowed.
forbid (
  principal,
  action == AgentCore::Action::"propose-switching-target___propose_switching",
  resource
)
when {
  context.input has action &&
  context.input.action == "energise" &&
  !(
    context.input has safety_clearance_id &&
    context.input.safety_clearance_id like "sfc_*" &&
    context.input has flood_check &&
    context.input.flood_check has intersects &&
    context.input.flood_check.intersects == false
  )
};

// R4.8, R2.5 defence against contact data reaching the tool at all.
forbid (
  principal,
  action == AgentCore::Action::"record-outage-target___record_outage",
  resource
)
when { context.input has callback_ref && context.input.callback_ref like "*@*" };

// R12.4 permits. One per tool, scoped to the agent role claim. Read-only tools are
// open to every Minnal agent; the two proposal tools are not.
permit (
  principal,
  action in [
    AgentCore::Action::"record-outage-target___record_outage",
    AgentCore::Action::"trace-upstream-device-target___trace_upstream_device",
    AgentCore::Action::"check-flood-geofence-target___check_flood_geofence",
    AgentCore::Action::"rank-restoration-jobs-target___rank_restoration_jobs",
    AgentCore::Action::"plan-crew-route-target___plan_crew_route"
  ],
  resource == AgentCore::Gateway::"{{GATEWAY_ARN}}"
)
when { principal.hasTag("minnal_role") };

// R9, R12.4 only the dispatch role may propose a dispatch.
permit (
  principal,
  action == AgentCore::Action::"dispatch-crew-target___dispatch_crew",
  resource == AgentCore::Gateway::"{{GATEWAY_ARN}}"
)
when { principal.hasTag("minnal_role") && principal.getTag("minnal_role") == "dispatch" };

// R10, R12.4 only the commander role may propose switching.
permit (
  principal,
  action == AgentCore::Action::"propose-switching-target___propose_switching",
  resource == AgentCore::Gateway::"{{GATEWAY_ARN}}"
)
when { principal.hasTag("minnal_role") && principal.getTag("minnal_role") == "commander" };

// R11.2 [SAFETY] no permit exists for any approval action, and none may be added:
// approving is not a Gateway tool at all. The Approval_Handler sits behind Cognito
// with a human-only group check.
