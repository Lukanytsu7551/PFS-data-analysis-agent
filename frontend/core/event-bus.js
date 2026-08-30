function createEventBus() {
  const listeners = new Map();

  function on(eventName, listener) {
    if (typeof listener !== "function") {
      throw new TypeError("Event listener must be a function");
    }
    const eventListeners = listeners.get(eventName) || new Set();
    eventListeners.add(listener);
    listeners.set(eventName, eventListeners);
    return () => off(eventName, listener);
  }

  function off(eventName, listener) {
    const eventListeners = listeners.get(eventName);
    if (!eventListeners) return false;
    const removed = eventListeners.delete(listener);
    if (!eventListeners.size) listeners.delete(eventName);
    return removed;
  }

  function once(eventName, listener) {
    const unsubscribe = on(eventName, (payload) => {
      unsubscribe();
      listener(payload);
    });
    return unsubscribe;
  }

  function emit(eventName, payload) {
    const eventListeners = listeners.get(eventName);
    if (!eventListeners?.size) return 0;
    let delivered = 0;
    for (const listener of [...eventListeners]) {
      try {
        listener(payload);
        delivered += 1;
      } catch (error) {
        console.error(`[event-bus] listener failed for ${eventName}`, error);
      }
    }
    return delivered;
  }

  function clear(eventName) {
    if (eventName === undefined) {
      listeners.clear();
      return;
    }
    listeners.delete(eventName);
  }

  return Object.freeze({ on, off, once, emit, clear });
}

const pfs = globalThis.PFS || {};
export const eventBus = pfs.events || createEventBus();
pfs.events = eventBus;
globalThis.PFS = pfs;
