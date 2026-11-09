type Direction = "up" | "down";
type NewsMove = { direction: Direction; label: string };

const majorIndex =
  /^(?:上证指数|沪指|深证成指|深成指|创业板指|科创50(?:指数)?|沪深300(?:指数)?|北证50(?:指数)?|(?:香港)?恒生(?:科技)?指数|(?:富时中国)?A50指数|(?:韩国(?:股市)?)?KOSPI指数|日经225指数|日本东证指数|台交所加权股价指数|MSCI亚太指数|标普500(?:指数)?|道琼斯(?:工业平均)?指数|纳斯达克(?:综合|100)?指数)(?:期货)?$/i;
const marketSubject =
  /(?:指数|期货|主力合约|股价|板块|概念(?:股)?|个股|股票|ETF|[A美港台日欧]股(?:市)?|金价|油价)$|^现货(?:黄金|白银|铜)$|^(?:台积电|英伟达|特斯拉|苹果|微软|亚马逊|谷歌|比亚迪|贵州茅台|宁德时代|SK海力士)$|^.{1,24}(?:股份|科技|电子|医药|制药|药业|食品|交通|集团|控股|能源|证券|保险|银行|汽车|新材|电气|电力|软件|通信|石化|矿业|公司)$/i;
const nonPriceMetric =
  /(?:收益率|利率|税率|费率|汇率|概率|物价|价格指数|通胀|成本|估值|成交额|成交量|净值|份额|资金|持仓|融资|余额|营收|业绩|净利|利润|销售|销量|产量|出口|进口|支出|收入|人数|家数|数量|规模|库存|运费|房价|租金|工资|同比|环比|目标价|景气|信心|恐慌|风险|PMI)/i;
const uncertain =
  /(?:预测|预计|预期|有望|或将|或可|可能|能否|将会|或有|会不会|如果|假如|明天|明日|尚未|未能|并未|没有|不再|未见|未触及|不涨|不跌|未涨|未跌|涨跌(?:不一|互现|参半)|冲高回落|先涨后跌|先跌后涨|(?:由|从)涨转跌|(?:由|从)跌转涨|扭转跌势|扭转涨势)|[？?]/;
const directionWords =
  /(?:大幅上涨|大幅下跌|大涨|大跌|暴涨|暴跌|飙涨|猛跌|上涨|下跌|高开|低开|拉升|回落|涨幅|跌幅|涨|跌)/;
const explicitMove = /^(?:大幅上涨|大幅下跌|大涨|大跌|暴涨|暴跌|飙涨|猛跌)$/;
const upWord = /涨|高开|拉升/;
const quoteClause = new RegExp(
  String.raw`^(?<subject>[\p{L}\p{N}*·（）()&.\-]{2,34}?)(?:早盘|午后|盘初|盘中|尾盘|收盘|开盘|日内|现|最新|夜盘|一度|今日)*(?<verb>${directionWords.source})(?:(?:扩大|达)?(?:至|达)?(?<qualifier>超过|超|逾|近|约)?(?<amount>\d+(?:\.\d+)?)%(?<bound>以上|以下)?)?`,
  "u",
);
const limitClause =
  /^(?<subject>[\p{L}\p{N}*·（）()&.\-]{2,34}?)(?:盘初|盘中|尾盘|开盘|收盘|竞价|午后|早盘|直线|双双|纷纷|集体|再度|再次)*(?<clearLimit>一字|20cm|30cm)?(?<touch>触及|触碰|封住|封死)?(?<limit>[涨跌]停)$/iu;
const opposingPriceMove = {
  up: /(?:下跌|大跌|暴跌|低开|跌停|跌幅|跳水|下挫|翻绿|跌(?:超|逾|近|约)?\d)/,
  down: /(?:上涨|大涨|暴涨|高开|涨停|涨幅|拉升|翻红|涨(?:超|逾|近|约)?\d)/,
};
const explicitMoveTail =
  /^(?:$|\d+(?:\.\d+)?(?:点|元)|至|报|创|刷新|突破|站上|失守|触及)/;

export function readNewsMove(title: string): NewsMove | undefined {
  const headline = title
    .trim()
    .replace(/^财联社\d{1,2}月\d{1,2}日电[，,：:]\s*/, "")
    .replace(/涨幅扩大[，,](?:现|最新)涨/g, "涨幅扩大至")
    .replace(/跌幅扩大[，,](?:现|最新)跌/g, "跌幅扩大至")
    .replace(/[％]/g, "%");
  if (uncertain.test(headline) || headline.startsWith("【")) return;

  const clauses = headline.split(/[，,、。；;！!：:\s]+/);
  const moves: NewsMove[] = [];
  for (const clause of clauses) {
    const limit = limitClause.exec(clause)?.groups;
    if (
      limit &&
      !nonPriceMetric.test(limit.subject) &&
      (marketSubject.test(limit.subject) ||
        (limit.clearLimit && /^[\p{L}\p{N}*]{2,10}$/u.test(limit.subject)))
    ) {
      const word = limit.limit;
      moves.push({
        direction: word === "涨停" ? "up" : "down",
        label: `${limit.touch === "触及" || limit.touch === "触碰" ? "触及" : ""}${word}`,
      });
      continue;
    }

    const quote = quoteClause.exec(clause);
    if (!quote?.groups) continue;
    const { subject, verb, qualifier, amount, bound } = quote.groups;
    if (nonPriceMetric.test(subject)) continue;
    const index = majorIndex.test(subject);
    if (!index && !marketSubject.test(subject)) continue;
    const numeric = Number(amount);
    const threshold = index ? 2 : 5;
    const crossesThreshold =
      numeric >= threshold &&
      bound !== "以下" &&
      !((qualifier === "近" || qualifier === "约") && numeric === threshold);
    const explicit =
      explicitMove.test(verb) &&
      (Boolean(amount) || explicitMoveTail.test(clause.slice(quote[0].length)));
    if (!explicit && !crossesThreshold) continue;

    const direction = upWord.test(verb) ? "up" : "down";
    const movement = direction === "up" ? "大涨" : "大跌";
    const sign = direction === "up" ? "+" : "-";
    moves.push({
      direction,
      label: amount
        ? `${movement} ${qualifier || (bound ? "" : sign)}${amount}%${bound ?? ""}`
        : movement,
    });
  }

  const first = moves[0];
  if (!first || opposingPriceMove[first.direction].test(headline)) return;
  return moves.length === 1
    ? first
    : {
        direction: first.direction,
        label: first.direction === "up" ? "大涨" : "大跌",
      };
}
