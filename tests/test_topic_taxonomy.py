import unittest

from skimmer.domain.topic_taxonomy import classify_title


class ClassifyTitleTests(unittest.TestCase):
    def test_strong_term_in_supporting_category_is_labelled(self):
        labels = classify_title("What Ozempic Actually Does To Your Metabolism", "27")
        self.assertIn("health", labels)

    def test_weak_terms_alone_never_produce_a_label(self):
        # "study", "research", "evidence" are overloaded; without a strong term
        # they must not create a label no matter how supportive the category.
        self.assertEqual(classify_title("A study on ancient history", "27"), {})
        self.assertEqual(classify_title("The evidence nobody saw coming", "25"), {})

    def test_scientists_clickbait_in_science_category_is_rejected(self):
        # The exact leak that made the first pass unusable.
        self.assertEqual(
            classify_title(
                "Scientists CONFIRMED This Is Our Strongest Evidence of Alien Life!", "28"
            ),
            {},
        )

    def test_topic_exclusion_is_scoped_to_one_topic(self):
        # "psychology of" is a format marker for behaviour only. It must not
        # discard the education label on a title that covers both.
        labels = classify_title(
            "New study: student loans and the psychology of debt avoidance", "27"
        )
        self.assertIn("education", labels)
        self.assertNotIn("behavior", labels)

    def test_entertainment_psychology_format_is_rejected(self):
        for title in (
            "The Psychology of Messi's Silent Dictatorship",
            "The Secret Psychology Behind Johnny Depp's Iconic Roles",
            "Prison Psychologist Says Her Cannibal Patient Is 'Shy'",
        ):
            self.assertNotIn("behavior", classify_title(title, "24"), msg=title)

    def test_practitioner_signal_survives(self):
        self.assertIn(
            "behavior",
            classify_title("How Harsh Parenting Changes Personality | Clinical Psychologist Explains", "22"),
        )

    def test_political_vaccine_content_is_not_health(self):
        # v2 dropped `vaccin\w*`: on YouTube it is political discourse.
        self.assertEqual(classify_title("Aaron Rodgers VINDICATED For Refusing COVID Vaccine!", "25"), {})

    def test_military_and_fitness_bootcamps_are_not_education(self):
        self.assertEqual(classify_title("Official U.S. Navy Bootcamp Graduation Livestream", "22"), {})

    def test_genre_exclusions_short_circuit_strong_terms(self):
        for title in (
            "Lofi Study Beats to Relax and Focus",
            "3-Hour Pomodoro Study With Me for Deep Focus & ADHD",
            "OLD SCHOOL R&B MIX 90s",
            "Wednesday Night Bible Study - James 1",
        ):
            self.assertEqual(classify_title(title, "10"), {}, msg=title)

    def test_noise_category_needs_corroboration(self):
        # Strong term alone in a noise category falls below threshold...
        self.assertEqual(classify_title("Creatine", "10"), {})
        # ...but a weak term alongside it restores the label.
        self.assertIn("health", classify_title("Creatine and protein for weight loss", "10"))

    def test_meta_science_terms_are_recognised(self):
        labels = classify_title("This meta-analysis has a sample size of 12", "27")
        self.assertIn("meta_science", labels)

    def test_multiple_topics_may_apply(self):
        labels = classify_title(
            "Student debt and the placebo effect on motivation", "27"
        )
        self.assertIn("education", labels)
        self.assertIn("behavior", labels)

    def test_missing_title_is_safe(self):
        self.assertEqual(classify_title(None, "27"), {})
        self.assertEqual(classify_title("", None), {})

    def test_category_may_be_absent(self):
        self.assertIn("health", classify_title("Do statins actually work?", None))


if __name__ == "__main__":
    unittest.main()
