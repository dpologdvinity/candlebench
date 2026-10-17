// Native trade walk for candlebench.engine.
//
// The Python engine spends nearly all of its time on interpreter overhead: one
// `_execute` call, four array slices and an `argmax` per trade, around 16 us
// each, over ~90k trades for a 200-trial 1m run. The signal loop cannot be
// vectorised in numpy because whether a signal trades depends on where the
// previous trade exited, so the loop moves here instead.
//
// Every result must equal the Python path bit for bit, which
// tests/test_engine_core.py checks. That rules out reassociating any
// arithmetic: each expression below is evaluated in the same order as its
// counterpart in engine.py, and the module is built with -ffp-contract=off so
// the compiler cannot fuse a multiply and an add into one rounding.

#include <pybind11/numpy.h>
#include <pybind11/pybind11.h>

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <stdexcept>
#include <vector>

namespace py = pybind11;

namespace {

using Prices = py::array_t<double, py::array::c_style | py::array::forcecast>;
using Indices = py::array_t<std::int64_t, py::array::c_style | py::array::forcecast>;

// Codes for engine.REASONS, in the same order.
enum Reason : std::int8_t { kStop = 0, kTarget = 1, kTimeout = 2, kSessionEnd = 3 };

struct Bars {
    const double* open;
    const double* high;
    const double* low;
    const double* close;
    std::int64_t n;
};

struct TradeRules {
    double reward_multiple;
    std::int64_t max_hold_bars;
    double min_risk_pct;
    const double* cost;  // one-way cost per bar; the last value covers later bars
    std::int64_t cost_len;
    double commission_r;
};

// Columns of closed trades, returned to Python as numpy arrays.
struct Trades {
    std::vector<std::int64_t> source;  // signal position or template index
    std::vector<std::int64_t> direction;
    std::vector<std::int64_t> entry_index;
    std::vector<std::int64_t> exit_index;
    std::vector<double> entry_price;
    std::vector<double> exit_price;
    std::vector<double> stop_price;
    std::vector<double> target_price;
    std::vector<double> risk;
    std::vector<std::int8_t> reason;
    std::vector<double> gross_r;
    std::vector<double> net_r;
    std::vector<double> return_pct;
    std::vector<double> cost_bps;
};

double slip_at(const TradeRules& rules, std::int64_t index) {
    return rules.cost[std::min(index, rules.cost_len - 1)];
}

// engine._resolve_exit over bars [start, last]: returns the exit offset and
// fill. The first bar touching either level decides, and the stop wins a tie
// unless that bar opened beyond the target.
void resolve_exit(const Bars& bars, std::int64_t start, std::int64_t last, int direction,
                  double stop, double target, bool hit_hold_limit, std::int64_t& offset,
                  double& fill, Reason& reason) {
    for (std::int64_t k = start; k <= last; ++k) {
        const double open = bars.open[k];
        bool stop_touch;
        bool target_touch;
        if (direction > 0) {
            stop_touch = bars.low[k] <= stop;
            target_touch = bars.high[k] >= target;
        } else {
            stop_touch = bars.high[k] >= stop;
            target_touch = bars.low[k] <= target;
        }
        if (stop_touch && target_touch) {
            const bool opened_past = direction > 0 ? open >= target : open <= target;
            if (opened_past) {
                offset = k - start;
                fill = open;
                reason = kTarget;
                return;
            }
        }
        if (stop_touch) {
            offset = k - start;
            fill = direction > 0 ? std::min(stop, open) : std::max(stop, open);
            reason = kStop;
            return;
        }
        if (target_touch) {
            offset = k - start;
            fill = direction > 0 ? std::max(target, open) : std::min(target, open);
            reason = kTarget;
            return;
        }
    }
    offset = last - start;
    fill = bars.close[last];
    reason = hit_hold_limit ? kTimeout : kSessionEnd;
}

// engine._execute: walk one trade from its entry bar and charge both legs.
// Returns the exit index.
std::int64_t execute(const Bars& bars, const TradeRules& rules, std::int64_t source,
                     std::int64_t start, int direction, double entry, double stop, double risk,
                     Trades& out) {
    const double d = static_cast<double>(direction);
    const double target = entry + d * risk * rules.reward_multiple;
    const std::int64_t hold_limit = start + rules.max_hold_bars - 1;
    const std::int64_t stop_index = std::min(hold_limit, bars.n - 1);
    const bool hit_hold_limit = hold_limit <= bars.n - 1;

    std::int64_t offset;
    double exit_price;
    Reason reason;
    resolve_exit(bars, start, stop_index, direction, stop, target, hit_hold_limit, offset,
                 exit_price, reason);
    const std::int64_t exit_index = start + offset;

    const double fill_entry = entry * (1 + d * slip_at(rules, start));
    const double fill_exit = exit_price * (1 - d * slip_at(rules, exit_index));

    out.source.push_back(source);
    out.direction.push_back(direction);
    out.entry_index.push_back(start);
    out.exit_index.push_back(exit_index);
    out.entry_price.push_back(fill_entry);
    out.exit_price.push_back(fill_exit);
    out.stop_price.push_back(stop);
    out.target_price.push_back(target);
    out.risk.push_back(risk);
    out.reason.push_back(reason);
    out.gross_r.push_back(d * (exit_price - entry) / risk);
    out.net_r.push_back(d * (fill_exit - fill_entry) / risk - rules.commission_r);
    out.return_pct.push_back(d * (fill_exit - fill_entry) / fill_entry -
                             rules.commission_r * risk / fill_entry);
    out.cost_bps.push_back((slip_at(rules, start) + slip_at(rules, exit_index)) / 2 * 10'000);
    return exit_index;
}

template <typename T>
py::array_t<T> to_array(const std::vector<T>& values) {
    py::array_t<T> array(static_cast<py::ssize_t>(values.size()));
    std::copy(values.begin(), values.end(), array.mutable_data());
    return array;
}

py::dict to_dict(const Trades& t) {
    py::dict out;
    out["source"] = to_array(t.source);
    out["direction"] = to_array(t.direction);
    out["entry_index"] = to_array(t.entry_index);
    out["exit_index"] = to_array(t.exit_index);
    out["entry_price"] = to_array(t.entry_price);
    out["exit_price"] = to_array(t.exit_price);
    out["stop_price"] = to_array(t.stop_price);
    out["target_price"] = to_array(t.target_price);
    out["risk_per_share"] = to_array(t.risk);
    out["reason"] = to_array(t.reason);
    out["gross_r"] = to_array(t.gross_r);
    out["net_r"] = to_array(t.net_r);
    out["return_pct"] = to_array(t.return_pct);
    out["cost_bps"] = to_array(t.cost_bps);
    return out;
}

Bars bars_of(const Prices& open, const Prices& high, const Prices& low, const Prices& close) {
    const std::int64_t n = close.size();
    if (open.size() != n || high.size() != n || low.size() != n) {
        throw std::invalid_argument("open, high, low and close differ in length");
    }
    return Bars{open.data(), high.data(), low.data(), close.data(), n};
}

TradeRules rules_of(double reward_multiple, std::int64_t max_hold_bars, double min_risk_pct,
                    const Prices& cost, double commission_r) {
    if (max_hold_bars < 1) {
        throw std::invalid_argument("max_hold_bars must be at least 1");
    }
    if (cost.size() < 1) {
        throw std::invalid_argument("cost needs at least one value");
    }
    return TradeRules{reward_multiple, max_hold_bars, min_risk_pct, cost.data(), cost.size(),
                      commission_r};
}

// engine.simulate's signal loop.
py::dict simulate(const Prices& open, const Prices& high, const Prices& low, const Prices& close,
                  const Prices& extremes, const Indices& signals, int direction, double stop_buffer,
                  double reward_multiple, std::int64_t max_hold_bars, double min_risk_pct,
                  bool allow_overlapping_trades, const Prices& cost, double commission_r) {
    const Bars bars = bars_of(open, high, low, close);
    const TradeRules rules = rules_of(reward_multiple, max_hold_bars, min_risk_pct, cost,
                                      commission_r);
    if (extremes.size() != bars.n) {
        throw std::invalid_argument("extremes differ in length from the bars");
    }
    const double* extreme_at = extremes.data();
    const std::int64_t* signal = signals.data();
    Trades out;
    std::int64_t next_allowed = 0;
    {
        py::gil_scoped_release release;
        for (py::ssize_t s = 0; s < signals.size(); ++s) {
            const std::int64_t i = signal[s];
            if (i < 0 || i + 1 >= bars.n) {
                continue;
            }
            if (!allow_overlapping_trades && i + 1 < next_allowed) {
                continue;
            }
            const double entry = bars.open[i + 1];
            const double extreme = extreme_at[i];
            if (!std::isfinite(entry) || !std::isfinite(extreme) || entry <= 0) {
                continue;
            }
            double stop;
            double risk;
            if (direction > 0) {
                stop = extreme * (1 - stop_buffer);
                risk = entry - stop;
            } else {
                stop = extreme * (1 + stop_buffer);
                risk = stop - entry;
            }
            if (risk <= 0 || risk / entry < min_risk_pct) {
                continue;
            }
            next_allowed = execute(bars, rules, s, i + 1, direction, entry, stop, risk, out) + 1;
        }
    }
    return to_dict(out);
}

// engine.simulate_matched after its entries are drawn: `starts[j]` is the
// entry bar drawn for template trade `templates[j]`.
py::dict simulate_matched(const Prices& open, const Prices& high, const Prices& low,
                          const Prices& close, const Indices& templates, const Indices& starts,
                          const Indices& entry_index, const Indices& directions,
                          const Prices& stop_price, double reward_multiple,
                          std::int64_t max_hold_bars, double min_risk_pct, const Prices& cost,
                          double commission_r) {
    const Bars bars = bars_of(open, high, low, close);
    const TradeRules rules = rules_of(reward_multiple, max_hold_bars, min_risk_pct, cost,
                                      commission_r);
    if (starts.size() != templates.size() || entry_index.size() != directions.size() ||
        stop_price.size() != directions.size()) {
        throw std::invalid_argument("template columns differ in length");
    }
    const std::int64_t* tmpl = templates.data();
    const std::int64_t* start_at = starts.data();
    const std::int64_t* entered = entry_index.data();
    const std::int64_t* dir = directions.data();
    const double* stops = stop_price.data();
    const std::int64_t count = directions.size();
    for (py::ssize_t j = 0; j < templates.size(); ++j) {
        const std::int64_t t = tmpl[j];
        const std::int64_t start = start_at[j];
        if (t < 0 || t >= count || start < 1 || start >= bars.n) {
            throw std::out_of_range("matched entry outside the session");
        }
        // Python indexing: the bar before a template entered at 0 is the last.
        std::int64_t before = entered[t] - 1;
        if (before < 0) {
            before += bars.n;
        }
        if (before < 0 || before >= bars.n) {
            throw std::out_of_range("template entry outside the session");
        }
    }
    Trades out;
    {
        py::gil_scoped_release release;
        for (py::ssize_t j = 0; j < templates.size(); ++j) {
            const std::int64_t t = tmpl[j];
            const std::int64_t start = start_at[j];
            const int direction = static_cast<int>(dir[t]);
            const double d = static_cast<double>(direction);
            const double entry = bars.open[start];
            if (!std::isfinite(entry) || entry <= 0) {
                continue;
            }
            std::int64_t before = entered[t] - 1;
            if (before < 0) {
                before += bars.n;
            }
            const double reach = d * (bars.close[before] - stops[t]);
            const double stop = bars.close[start - 1] - d * reach;
            const double risk = d * (entry - stop);
            if (risk <= 0 || risk / entry < min_risk_pct) {
                continue;
            }
            execute(bars, rules, t, start, direction, entry, stop, risk, out);
        }
    }
    return to_dict(out);
}

}  // namespace

PYBIND11_MODULE(_enginecore, m) {
    m.doc() = "Native trade walk for candlebench.engine; see engine.py for the rules.";
    m.def("simulate", &simulate, py::arg("open"), py::arg("high"), py::arg("low"),
          py::arg("close"), py::arg("extremes"), py::arg("signals"), py::arg("direction"),
          py::arg("stop_buffer"), py::arg("reward_multiple"), py::arg("max_hold_bars"),
          py::arg("min_risk_pct"), py::arg("allow_overlapping_trades"), py::arg("cost"),
          py::arg("commission_r"));
    m.def("simulate_matched", &simulate_matched, py::arg("open"), py::arg("high"),
          py::arg("low"), py::arg("close"), py::arg("templates"), py::arg("starts"),
          py::arg("entry_index"), py::arg("directions"), py::arg("stop_price"),
          py::arg("reward_multiple"), py::arg("max_hold_bars"), py::arg("min_risk_pct"),
          py::arg("cost"), py::arg("commission_r"));
}
