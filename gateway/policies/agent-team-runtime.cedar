// ---------------------------------------------------------------------------
// Minnal agent-team-runtime Cedar policy: role-scoped permits for the four
// read-only status tools this spec owns (get_flood_status, list_open_outages,
// get_proposal_status, list_crews). To be merged into the file grid-tools
// creates (C3, C10); adds no forbid and alters no existing rule.
//
// Action names follow the `<TargetName>___<tool_name>` form, matching
// gateway_tool_name (design §8.1.1): tool in kebab-case + `-target` + `___` +
// tool. Every principal read is guarded with `has` before `getTag`, per the
// Cedar limits grid-tools §10.2 records: an unguarded read of an absent tag is
// an evaluation error, not a clean deny.
// ---------------------------------------------------------------------------

// R14.10: get_flood_status is readable by the situation (hazard) and safety roles.
permit (
  principal,
  action in [
    AgentCore::Action::"get-flood-status-target___get_flood_status"
  ],
  resource == AgentCore::Gateway::"{{GATEWAY_ARN}}"
) when {
  principal has minnal_role &&
  (principal.getTag("minnal_role") == "hazard" ||
   principal.getTag("minnal_role") == "safety")
};

// R14.10: list_open_outages is readable by the diagnostics role only.
permit (
  principal,
  action in [
    AgentCore::Action::"list-open-outages-target___list_open_outages"
  ],
  resource == AgentCore::Gateway::"{{GATEWAY_ARN}}"
) when {
  principal has minnal_role && principal.getTag("minnal_role") == "diagnostics"
};

// R14.10: get_proposal_status is readable by the commander role only. It cannot approve.
permit (
  principal,
  action in [
    AgentCore::Action::"get-proposal-status-target___get_proposal_status"
  ],
  resource == AgentCore::Gateway::"{{GATEWAY_ARN}}"
) when {
  principal has minnal_role && principal.getTag("minnal_role") == "commander"
};

// R14.10 and C10: list_crews is readable by the dispatch role only.
permit (
  principal,
  action in [
    AgentCore::Action::"list-crews-target___list_crews"
  ],
  resource == AgentCore::Gateway::"{{GATEWAY_ARN}}"
) when {
  principal has minnal_role && principal.getTag("minnal_role") == "dispatch"
};
