"use strict";

function roleName(role) {
  return { admin: "管理员", manager: "项管", member: "成员" }[role] || role;
}

function memberGroups(member) {
  const names = (member.group_ids || []).map((id) => groupById(id)?.name).filter(Boolean);
  if (!names.length && member.group_name) names.push(member.group_name);
  return names;
}

function priorityCell(priority) {
  const level = { 高: "high", 中: "mid", 低: "low" }[priority] || "mid";
  return `<span class="prio prio-${level}"><i class="dot"></i>${esc(priority)}</span>`;
}
