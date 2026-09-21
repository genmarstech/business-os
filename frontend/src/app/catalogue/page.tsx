import Link from "next/link";
import { redirect } from "next/navigation";

import { Shell } from "@/components/Shell";
import { PERM, may, me as whoAmI } from "@/lib/session";
import { catalogue } from "./data";
import { CategoryForm, ProductForm } from "./forms";
import { categoryOf, margin, money, type Product, type TaxRule } from "./shape";
import styles from "./catalogue.module.css";

/**
 * What the shop sells.
 *
 * ── MARGIN IS SHOWN, AND IT IS THE NET ONE ─────────────────────────────────
 * A catalogue that only lists prices makes somebody open a calculator to
 * answer the question they actually came with. The figure has to be the
 * after-tax one: on an inclusive rule a 116 shelf price is 100 of revenue, and
 * taking cost off 116 overstates the margin on every line by the VAT rate.
 * See shape.ts — the arithmetic is there, tested by being in one place.
 */

export const dynamic = "force-dynamic";

export const metadata = { title: "Catalogue" };

export default async function CataloguePage({
  searchParams,
}: {
  searchParams: Promise<{ edit?: string }>;
}) {
  const me = await whoAmI();
  if (!me) redirect("/");

  const { edit } = await searchParams;
  const { products, categories, taxRules } = await catalogue();
  const organisationId =
    me.kind === "subscriber" ? me.organisations[0]?.id : me.organisation.id;

  const canManage = may(me, PERM.catalogManage);
  const editing = edit ? products.find((p) => p.id === Number(edit)) : undefined;
  const rules = new Map(taxRules.map((rule) => [rule.id, rule]));

  return (
    <Shell me={me}>
      <div className={styles.page}>
        <header className={styles.head}>
          <p className={styles.eyebrow}>Inventory</p>
          <h1 className={styles.title}>Catalogue</h1>
          <p className={styles.sub}>
            Every product, its price and the tax it is quoted against. Changing
            a price here changes what the next sale charges — it never rewrites
            a receipt that has already been printed.
          </p>
        </header>

        <section className={styles.panel}>
          <h2 className={styles.panelTitle}>
            {products.length === 0
              ? "Nothing in the catalogue yet"
              : `${products.length} product${products.length === 1 ? "" : "s"}`}
          </h2>

          {products.length === 0 ? (
            <p className={styles.empty}>
              A till can only sell what is listed here. Add the first one
              below.
            </p>
          ) : (
            <div className={styles.scroll}>
              <table className={styles.table}>
                <thead>
                  <tr>
                    <th>Product</th>
                    <th>Category</th>
                    <th className={styles.num}>Shelf</th>
                    <th className={styles.num}>Cost</th>
                    <th className={styles.num}>Margin</th>
                    <th>Tax</th>
                    {canManage ? <th /> : null}
                  </tr>
                </thead>
                <tbody>
                  {products.map((product) => (
                    <Line
                      key={product.id}
                      product={product}
                      rule={
                        product.tax_rule
                          ? rules.get(product.tax_rule)
                          : undefined
                      }
                      categoryName={
                        categories.find((c) => c.id === categoryOf(product))
                          ?.name
                      }
                      canManage={canManage}
                    />
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>

        {canManage && organisationId ? (
          <>
            <section className={styles.panel} id="product">
              <h2 className={styles.panelTitle}>
                {editing ? `Edit ${editing.name}` : "Add a product"}
              </h2>
              {editing ? (
                <p className={styles.panelLede}>
                  <Link className={styles.quiet} href="/catalogue">
                    Add a different one instead
                  </Link>
                </p>
              ) : null}

              {categories.length === 0 ? (
                <p className={styles.empty}>
                  Add a category first — every product belongs to one.
                </p>
              ) : (
                <ProductForm
                  key={editing?.id ?? "new"}
                  organisationId={organisationId}
                  categories={categories}
                  taxRules={taxRules}
                  product={editing}
                />
              )}
            </section>

            <section className={styles.panel}>
              <h2 className={styles.panelTitle}>Categories</h2>
              <p className={styles.panelLede}>
                How the till groups products on screen. Somebody at a counter
                finds things by category faster than by typing, so these are
                worth getting right.
              </p>

              {categories.length > 0 ? (
                <ul className={styles.chips}>
                  {categories.map((category) => (
                    <li key={category.id} className={styles.chip}>
                      {category.name}
                      <span className={styles.chipCount}>
                        {
                          products.filter((p) => categoryOf(p) === category.id)
                            .length
                        }
                      </span>
                    </li>
                  ))}
                </ul>
              ) : null}

              <CategoryForm organisationId={organisationId} />
            </section>
          </>
        ) : null}

        <p className={styles.footnote}>
          Tax rules live on{" "}
          <Link className={styles.quiet} href="/settings/tax">
            their own screen
          </Link>
          , because a rate belongs to the business rather than to any one
          product.
        </p>
      </div>
    </Shell>
  );
}

function Line({
  product,
  rule,
  categoryName,
  canManage,
}: {
  product: Product;
  rule?: TaxRule;
  categoryName?: string;
  canManage: boolean;
}) {
  const m = margin(product, rule);

  return (
    <tr className={product.is_active ? "" : styles.withdrawn}>
      <td>
        <div className={styles.name}>{product.name}</div>
        <div className={styles.meta}>
          <span className={styles.mono}>{product.sku}</span>
          {product.barcode ? (
            <>
              {" · "}
              <span className={styles.mono}>{product.barcode}</span>
            </>
          ) : null}
          {product.is_active ? null : " · withdrawn"}
        </div>
      </td>
      <td>{categoryName ?? "—"}</td>
      <td className={styles.num}>{money(Number(product.selling_price))}</td>
      <td className={styles.num}>{money(Number(product.cost_price))}</td>
      <td className={styles.num}>
        {m === null ? (
          "—"
        ) : (
          <>
            <div className={m.profit < 0 ? styles.loss : undefined}>
              {money(m.profit)}
            </div>
            <div className={styles.meta}>{m.percent.toFixed(1)}%</div>
          </>
        )}
      </td>
      <td>
        {rule ? (
          <>
            {rule.rate}%
            <div className={styles.meta}>
              {rule.is_inclusive ? "in the price" : "added on"}
            </div>
          </>
        ) : (
          "None"
        )}
      </td>
      {canManage ? (
        <td className={styles.num}>
          <Link
            className={styles.quiet}
            href={`/catalogue?edit=${product.id}#product`}
          >
            Edit
          </Link>
        </td>
      ) : null}
    </tr>
  );
}
