(set-logic NRAT)
(declare-fun a () Real)
(declare-fun b () Real)
(declare-fun log_a () Real)
(declare-fun log_b () Real)
; We represent log(a) and log(b) using the constraint that exp(log_x) = x
(assert (> a 0))
(assert (> b 0))
(assert (= (exp log_a) a))  ; log_a represents log(a)
(assert (= (exp log_b) b))  ; log_b represents log(b)
(assert (< log_a log_b))    ; log(a) < log(b)
(assert (not (< a b)))      ; negation of our conclusion
(check-sat)
