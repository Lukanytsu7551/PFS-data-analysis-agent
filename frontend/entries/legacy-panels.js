import { installMcpPanel, mcp } from "../features/mcp.js";
import { installKnowledgePanel, knowledge } from "../features/knowledge.js";
import { businessCanvas } from "../features/business-canvas.js";

const pfs = globalThis.PFS || {};
pfs.mcp = mcp;
pfs.knowledge = knowledge;
pfs.businessCanvas = businessCanvas;
globalThis.PFS = pfs;

installMcpPanel();
installKnowledgePanel();
