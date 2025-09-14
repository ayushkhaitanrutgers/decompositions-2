(set-logic NRA)
; We want to prove: ∃C > 0 such that ∀a,b (a ≤ b → a ≤ C*(b/2))
; We negate this: ∀C > 0, ∃a,b such that (a ≤ b ∧ a > C*(b/2))
; In SMT: for any C > 0, there exist a,b violating the property

(declare-fun C () Real)
(assert (> C 0))
; The negation: there exist a,b such that a ≤ b but a > C*(b/2)
(assert (exists ((a Real) (b Real)) 
  (and (<= a b) (> a (* C (/ b 2))))))
(check-sat)
