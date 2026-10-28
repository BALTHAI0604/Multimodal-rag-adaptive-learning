// Run:  cd firestore-rules-test ; npm install ; npm test      (needs Java 11+ and internet once, for the emulator jar)
// NOT executed in the build environment (the emulator jar could not be downloaded there).
import { initializeTestEnvironment, assertFails, assertSucceeds } from "@firebase/rules-unit-testing";
import { doc, getDoc, setDoc, updateDoc, deleteDoc, collection, getDocs } from "firebase/firestore";
import { readFileSync } from "node:fs";
import { before, after, beforeEach, test } from "node:test";

let env;
before(async () => {
  env = await initializeTestEnvironment({
    projectId: "demo-adaptive",
    firestore: { rules: readFileSync(new URL("../firestore.rules", import.meta.url), "utf8"), host: "127.0.0.1", port: 8080 },
  });
});
after(async () => env.cleanup());
beforeEach(async () => {
  await env.clearFirestore();
  await env.withSecurityRulesDisabled(async (ctx) => {            // the backend (Admin SDK) writes these mirrors
    const db = ctx.firestore();
    await setDoc(doc(db, "users/alice"), { role: "student", active: true });
    await setDoc(doc(db, "users/bob"), { role: "faculty", active: true });
    await setDoc(doc(db, "grades/g1"), { score: 90 });
  });
});

const as = (uid) => env.authenticatedContext(uid).firestore();
const anon = () => env.unauthenticatedContext().firestore();

test("a user can read their own mirror", () => assertSucceeds(getDoc(doc(as("alice"), "users/alice"))));
test("a user cannot read someone else's mirror", () => assertFails(getDoc(doc(as("alice"), "users/bob"))));
test("anonymous users cannot read anything", () => assertFails(getDoc(doc(anon(), "users/alice"))));
test("role escalation is impossible: client cannot write its own role", async () => {
  await assertFails(updateDoc(doc(as("alice"), "users/alice"), { role: "admin" }));
  await assertFails(setDoc(doc(as("alice"), "users/alice"), { role: "admin", active: true }));
});
test("a client cannot create or delete user mirrors", async () => {
  await assertFails(setDoc(doc(as("carol"), "users/carol"), { role: "admin", active: true }));
  await assertFails(deleteDoc(doc(as("alice"), "users/alice")));
});
test("all other collections are closed to clients", async () => {
  await assertFails(getDoc(doc(as("alice"), "grades/g1")));
  await assertFails(setDoc(doc(as("alice"), "grades/g2"), { score: 100 }));
  await assertFails(getDocs(collection(as("alice"), "users")));          // listing is not allowed either
});
