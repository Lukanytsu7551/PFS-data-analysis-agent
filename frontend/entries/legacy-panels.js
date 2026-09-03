import { installMcpPanel, mcp } from "../features/mcp.js";
import { installKnowledgePanel, knowledge } from "../features/knowledge.js";

const pfs = globalThis.PFS || {};
pfs.mcp = mcp;
pfs.knowledge = knowledge;
globalThis.PFS = pfs;

installMcpPanel();
installKnowledgePanel();
