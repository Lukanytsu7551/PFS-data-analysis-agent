import { chatStream } from "../features/chat-stream.js";

const pfs = globalThis.PFS || {};
pfs.chatStream = chatStream;
globalThis.PFS = pfs;
