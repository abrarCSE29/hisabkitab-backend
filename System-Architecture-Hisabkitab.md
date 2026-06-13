# Architectural Blueprint: HisabKitab (হিসাবকিতাব)

## 1. Project Name
**HisabKitab (হিসাবকিতাব)** — Family Expense Tracker

## 2. Project Overview
HisabKitab is a mobile-first, web-responsive family expense tracker designed to digitize daily transactions for solo users and families in Dhaka, Bangladesh. The architecture is engineered to run entirely within the **free tiers** of modern cloud services while maintaining consistent uptime. 

By utilizing **MongoDB Atlas (M0 Free Tier)**, HisabKitab adopts a flexible, document-based architecture that perfectly models "vouchers" containing embedded, multi-line items as single atomic documents. This eliminates the need for expensive relational joins or migration managers. **FastAPI** serves as the backend api layer, utilizing the official **PyMongo** driver to execute database operations. **Next.js** forms the client layer, running on Vercel. User Authentication and Image Storage are offloaded to **Supabase** free tiers. Receipts are parsed using an external OpenAI-based Vision API.

---

## 3. Tech Stack
*   **Frontend:** Next.js (App Router, React 19) deployed on **Vercel** (Free Tier).
*   **Backend:** FastAPI (Python 3.11+) deployed on **Koyeb** or **Render** (Free Tier).
*   **Primary Database:** MongoDB Atlas M0 Sandbox (Free Tier, 512MB storage, AWS/GCP/Azure).
*   **Database Client:** PyMongo.
*   **Authentication:** Supabase Auth (Free Tier, up to 50,000 Monthly Active Users).
*   **Storage:** Supabase Storage (Free Tier, 1GB storage for compressed receipts).
*   **Image Compression:** Client-side modular utility using the `browser-image-compression` library.
*   **OCR Engine:** OpenAI API (`gpt-4o-mini` with JSON structured outputs) executed via the FastAPI backend.
*   **Local Dev Tooling:** Docker-compose (with a local MongoDB container).

---

## 4. Database
*   **Primary Database:** MongoDB Atlas.
*   **Data Models & Relationships:** To optimize queries and preserve atomic writes, HisabKitab uses an **Embedded Document Model**. Vouchers and their corresponding items are stored in a single document inside the `vouchers` collection.

### Collection Schema Design

#### `families` Collection
Stores metadata for users who upgrade from solo to shared tracking.
```json
{
  "_id": "ObjectId",
  "name": "String (e.g., 'Amader Songshar')",
  "created_by": "String (Supabase User UUID)",
  "members": [
    {
      "user_id": "String (Supabase User UUID)",
      "role": "String (Enum: 'admin' | 'member')"
    }
  ],
  "created_at": "ISODate"
}
```
*Index Strategy:* Unique index on `_id`, and a multi-key index on `members.user_id` to quickly fetch a user's associated families.

#### `vouchers` Collection
Stores individual transaction logs.
```json
{
  "_id": "ObjectId",
  "family_id": "ObjectId (Nullable, populated only in Family Mode)",
  "user_id": "String (Supabase User UUID of creator)",
  "type": "String (Enum: 'income' | 'expense')",
  "category_id": "String (e.g., 'bazaar', 'transport', 'dining')",
  "items": [
    {
      "name": "String",
      "amount": "Double"
    }
  ],
  "voucher_total": "Double (Pre-calculated sum of items.amount)",
  "image_url": "String (Nullable, Supabase Storage Public URL)",
  "created_at": "ISODate"
}
```
*Index Strategy:* Compound index on `{ family_id: 1, created_at: -1 }` and `{ user_id: 1, created_at: -1 }` to optimize reverse-chronological dashboard feeds.

### Migration Strategy
Because MongoDB is dynamic and schema-less, traditional migration tools like Alembic or Flyway are not required. Schema adjustments are managed directly in the application layer by assigning sensible defaults to newly added fields in the backend's validation schemas (Pydantic v2).

---

## 5. Caching Database and Strategies
To maintain zero-cost deployment limits, no Redis instance is provisioned. Instead, the system implements:
*   **Next.js Client-side Session State:** Zustand caches the last active workspace state (solo vs. family) and the current month's aggregates, minimizing redundant round-trips when navigating the UI.
*   **Covered Queries:** Database queries are designed to target indexed fields (`family_id`, `user_id`, `created_at`), allowing MongoDB to return matching documents directly from memory index structures.

---

## 6. High Level System Design

```
                                  [ User / Web Browser ]
                                             |
             +-------------------------------+-------------------------------+
             | (HTTPS / Client Traffic)                                      | (Auth Requests)
             v                                                               v
   +-------------------+                                           +-------------------+
   |  Vercel Frontend  |                                           |   Supabase Auth   |
   |    (Next.js)      | <=======================================> | (JWT Handshake &  |
   +-------------------+                                           |  Session Tokens)  |
             |                                                     +-------------------+
             | (API Requests with Authorization: Bearer JWT)
             v
   +-------------------+
   |  FastAPI Backend  | <--- Decodes JWT locally using Supabase RS256 JWKS
   |  (Render/Koyeb)   |
   +-------------------+
     |        |      |
     |        |      +------------(Read/Write Documents via PyMongo)-----+
     |        |                                                          v
     |        +---(OCR Processing Request)---> +---------------------------------+
     |                                         |       External OpenAI API       |
     |                                         | (JSON Structured Outputs Vision)|
     |                                         +---------------------------------+
     v
+---------------------------------------+
|            Supabase BaaS              |
|  +---------------------------------+  |
|  |  Storage Bucket (1GB - Receipts)|  |
|  +---------------------------------+  |
+---------------------------------------+
     |
     | (Query Database)
     v
+---------------------------------------+
|          MongoDB Atlas (M0)           |
|  +---------------------------------+  |
|  |  vouchers & families Collections|  |
|  +---------------------------------+  |
+---------------------------------------+
```

---

## 7. Functional Requirements

### [FR-1: Authentication & Session Management]
*   **Description:** End-users must sign in via Google OAuth or standard Email/Password. Supabase Auth handles registration, session persistence, and secure token issuance.
*   **Priority:** Critical (P0)

### [FR-2: Quick & Multi-Item Voucher Logging]
*   **Description:** A user can tap the `+` button, enter an amount, and save a voucher immediately. Users can optionally append separate itemized rows within a single voucher.
*   **Priority:** Critical (P0)

### [FR-3: Localized Expense Categorization]
*   **Description:** The complete voucher is tagged with a single category. Categories use both Bangla and English naming conventions (e.g., *Bazaar (বাজার)*, *Dining & Snacks (খাওয়া-দাওয়া)*).
*   **Priority:** High (P1)

### [FR-4: Optional Shared Family Space]
*   **Description:** Users default to a personal dashboard. They can choose to create a "Family" group and invite participants. When active, dashboards compile and aggregate joint transaction logs.
*   **Priority:** High (P1)

### [FR-5: Image Upload with Modular Client Compression]
*   **Description:** Users can capture receipt photos. Images are compressed client-side before upload to Supabase Storage, keeping bucket utilization within the 1GB limit.
*   **Priority:** Medium (P2)

### [FR-6: Intelligent OCR Parsing]
*   **Description:** Activating OCR on a receipt image triggers the backend to submit the image to the external OpenAI Vision API, returning structured arrays of extracted items to the frontend.
*   **Priority:** Medium (P2)

---

## 8. Features

### PyMongo Connection Lifecycle
*   *Associated FR:* None (System Core)
*   *Description:* FastAPI manages connection pooling with PyMongo using startup and shutdown events to avoid exhausting connections on MongoDB Atlas's free tier.
*   *FastAPI Sync Execution:* Standard `def` route definitions in FastAPI are run in an external thread pool by the framework, preventing synchronous PyMongo calls from blocking the async event loop.

### Modular Client Compression
*   *Associated FR:* [FR-5]
*   *Description:* An isolated modular block `utils/compression.ts` handles client-side resizing and quality adjustment of images. Developers can bypass this processing step by editing an environment toggle.

### OpenAI receipt extraction
*   *Associated FR:* [FR-6]
*   *Description:* Converts raw invoice data to clean, structured elements using structured model parsing through the OpenAI API.

---

## 9. Frontend Requirements

### Architecture
*   **Next.js App Router:** Static layouts and skeleton structures render on the server, while complex transaction builders and filter controllers leverage Client Components (`'use client'`).
*   **Mobile-First Interface:** Fluid, responsive styling via TailwindCSS. Touch-targets and action banners are optimized for single-hand mobile usage (360px grid target).

### Modular Compression Setup
The client-side image processing utility is decoupled from UI pages.

```typescript
// src/utils/compression.ts
import imageCompression from 'browser-image-compression';

export async function processReceiptImage(file: File): Promise<File | Blob> {
  const isEnabled = process.env.NEXT_PUBLIC_ENABLE_COMPRESSION === 'true';
  
  if (!isEnabled) {
    return file; // Bypass compression, returning original file
  }

  const options = {
    maxSizeMB: 0.15, // Keep files under 150KB for rapid upload
    maxWidthOrHeight: 1024,
    useWebWorker: true,
  };

  try {
    return await imageCompression(file, options);
  } catch (error) {
    console.warn("Compression helper failed, processing raw image file:", error);
    return file; 
  }
}
```

### State Management
*   **Zustand:** Controls UI display transitions, current family session scopes, and dashboard data caches to avoid unnecessary backend fetches.

---

## 10. API Endpoints Needed

All endpoints (excluding public health check routes) validate authorization payloads using Supabase JWT tokens.

| Method | Endpoint | Description | Auth Required | Payload / Query Params | Expected Response (200/201) |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `GET` | `/api/v1/health` | Health Check endpoint | No | None | `{"status": "healthy"}` |
| `POST` | `/api/v1/vouchers` | Create new Voucher | Yes | `{ "type": "expense", "category_id": "bazaar", "items": [{"name": "A", "amount": 10}], "image_url": "string" }` | `{"status": "success", "id": "65cb7f..."}` |
| `GET` | `/api/v1/vouchers` | Retrieve vouchers (Filtered) | Yes | `?family_id=65cb7f...&limit=20` | `[{"_id": "65cb7f...", "items": [...]}]` |
| `POST` | `/api/v1/vouchers/ocr` | Read receipt images via OpenAI | Yes | `{ "image_url": "string" }` | `{"items": [{"name": "item", "amount": 20}]}` |
| `POST` | `/api/v1/family` | Establish family entity | Yes | `{ "name": "Amader Songshar" }` | `{"family_id": "65cb7f...", "name": "Amader Songshar"}` |
| `POST` | `/api/v1/family/invite`| Send join code link via email | Yes | `{ "email": "user@domain.com" }` | `{"status": "invited"}` |

---

## 11. Test Cases Needed to Cover

### Unit Tests
*   **PyMongo Initialization Security:** Confirm that standard PyMongo connections apply secure timeout policies and restrict write operations to authorized user objects.
*   **Compression Toggle Utility:** Test `processReceiptImage` with `NEXT_PUBLIC_ENABLE_COMPRESSION` set to false. Confirm it bypasses file shrinking.
*   **Voucher Mathematical Summation:** Validate that saving a voucher automatically calculates the exact sum of nested item amounts without precision drift.

### Integration/Business Logic Tests
*   **Solo to Family Visibility Transition:** Verify that a user's transaction list remains scoped to their unique ID, changing to incorporate family records only when an active `family_id` context is set.
*   **Supabase JWT Expiry Acceptance:** Verify that the FastAPI backend rejects requests when passed an expired Supabase Auth token, returning an `HTTP 401 Unauthorized` status.

### Edge Cases
*   **Zero-Cost OpenAI Rate Limits:** Verify that the backend handles rate-limit and payment failures from the OpenAI API gracefully, returning structured validation responses to the client instead of generating application errors.

---

## 12. How Integration Should Be Tested

### Testing Frameworks
*   **Backend Validation:** `PyTest` combined with local Mock databases.
*   **Frontend Validation:** `Jest` for helper components, and `Playwright` to run end-to-end flow checks across simulated mobile devices.

### Environment Mocking
*   **Database Operations:** Integration tests utilize PyTest fixtures that substitute the real MongoDB Atlas database with a local database running via Docker.
*   **External APIs:** Mock handlers intercept outbound calls to OpenAI and Supabase during pipeline testing to prevent request overhead.

---

## 13. Deployment Strategy

### Deployment Pipeline
To provide a free, zero-cost architecture without cold-start delays, the backend uses a keep-alive polling script.

```
+------------------+     Push     +------------------+     Trigger     +---------------------+
| Developer Laptop | -----------> |  GitHub Registry | --------------> | Vercel (Next.js FE) |
+------------------+              +------------------+                 +---------------------+
                                           |
                                           | Trigger
                                           v
                                  +------------------+
                                  | Koyeb/Render BE  | <-----+
                                  | (FastAPI + PyM)  |       |
                                  +------------------+       | Keep-Alive Pings
                                                             | (Every 10 minutes)
                                  +------------------+       |
                                  |  Cron-Job.org    | ------+
                                  | (Free Cron Ping) |
                                  +------------------+
```

1.  **Deployment Platforms:** The Next.js frontend is hosted on **Vercel** (Free Tier). The FastAPI app is hosted on **Koyeb** or **Render** (Free Tier).
2.  **Uptime Management:** Free-tier application containers on Render or Koyeb sleep after 15 minutes of inactivity. To prevent this, a free cron service (such as **Cron-Job.org**) is scheduled to query `/api/v1/health` every 10 minutes. This keeps the application container awake during standard usage hours in Dhaka without incurring hosting costs.
3.  **Environment Variables:** Env vars (`MONGODB_URI`, `OPENAI_API_KEY`, `SUPABASE_URL`, `SUPABASE_JWT_SECRET`) are configured directly in the Vercel and Koyeb/Render project settings dashboards.

---

## 14. How to Deliver the Product

### Phased Milestones

```
+---------------------------------------------------------------------------------+
|                                 PHASE 1: Core MVP                               |
| - Connect PyMongo client pool in FastAPI                                        |
| - Develop core endpoints for vouchers inside MongoDB Atlas                      |
| - Deploy responsive Next.js frontend with Solo dashboard layout                 |
+---------------------------------------------------------------------------------+
                                         |
                                         v
+---------------------------------------------------------------------------------+
|                              PHASE 2: Shared Space                              |
| - Add optional Family tracking profile logic in MongoDB                         |
| - Implement image compression utility with toggle configurations                |
| - Configure Supabase Storage buckets                                            |
+---------------------------------------------------------------------------------+
                                         |
                                         v
+---------------------------------------------------------------------------------+
|                          PHASE 3: Intelligence & Uptime                         |
| - Connect OpenAI receipt OCR parsing in FastAPI                                 |
| - Deploy Keep-Alive Cron scheduler                                              |
| - Perform final system verification runs                                        |
+---------------------------------------------------------------------------------+
```

### Branching & Git Workflow
*   **Trunk-Based Development:** Developers push short-lived feature branches (`feature/voucher-logging`) to the remote repository. Continuous Integration workflows execute automatically before merging code into the `main` branch.

### Handover Artifacts
*   **Clean Repository:** Next.js frontend and FastAPI backend applications nested in a monorepo setup.
*   **Configuration Schema:** `.env.example` detailing configuration parameters.
*   **Local Launch Manifest:** `docker-compose.yml` to spin up a local MongoDB environment for testing.
*   **Postman Collection:** Configured collection to quickly test API endpoints.